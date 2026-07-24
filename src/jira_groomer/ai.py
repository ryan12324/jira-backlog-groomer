from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from openai import OpenAI

from .config import AIConfig, ProductConfig
from .errors import AIError
from .models import AssessmentBatch, IssueAssessment, JiraIssue

SYSTEM_PROMPT = """You are a senior product owner and Agile delivery coach grooming a
single backlog for a cross-functional frontend/backend team.

For every source issue:
- Preserve the intended user outcome. Never invent product behavior; put uncertainty in
  open_questions.
- Produce one vertical user story joining frontend, backend, data, and operational work where
  relevant. Do not create separate FE and BE stories for the same outcome.
- Apply INVEST: independent, negotiable, valuable, estimable, small, and testable.
- Make acceptance criteria observable and use Given/When/Then fields without repeating those
  words inside the field values.
- Include accessibility, privacy/security, analytics, observability, failure states, rollout,
  and performance only when relevant; otherwise do not add boilerplate.
- Recommend split only when the outcome cannot be delivered as one small demonstrable slice.
  Every split story must itself deliver user value and remain cross-functional.
- Recommend archive_candidate only when the supplied issue contains concrete evidence that it
  is obsolete, already delivered, superseded, or a confirmed duplicate. Staleness alone is not
  proof. List the evidence; deterministic policy will make the final decision.
- Link suggestions may target only the candidate keys supplied for that issue. Use duplicate
  only for the same underlying outcome, not merely related work.
- Scores must reflect supplied evidence. A job_size of 1 is smallest and 10 is largest.
- invest.overall_quality scores the source ticket before your rewrite, not the generated story.
- Return exactly one assessment for every source_key and no assessments for other keys.

This is decision support, not authorization to mutate Jira."""


class OpenAIBacklogAnalyst:
    def __init__(self, ai_config: AIConfig, product: ProductConfig) -> None:
        if not os.getenv("OPENAI_API_KEY"):
            raise AIError("OPENAI_API_KEY is required to generate a grooming plan")
        self.config = ai_config
        self.product = product
        self.client = OpenAI()

    def analyze(
        self,
        issues: list[JiraIssue],
        candidate_map: dict[str, list[JiraIssue]],
    ) -> list[IssueAssessment]:
        batches = [
            issues[start : start + self.config.batch_size]
            for start in range(0, len(issues), self.config.batch_size)
        ]
        if not batches:
            return []

        collected: dict[str, IssueAssessment] = {}
        with ThreadPoolExecutor(max_workers=self.config.max_parallel_requests) as executor:
            futures = {
                executor.submit(self._analyze_batch, batch, candidate_map): batch
                for batch in batches
            }
            for future in as_completed(futures):
                batch = futures[future]
                try:
                    assessments = future.result()
                except Exception as exc:
                    keys = ", ".join(issue.key for issue in batch)
                    if isinstance(exc, AIError):
                        raise
                    raise AIError(f"AI analysis failed for {keys}: {exc}") from exc
                for assessment in assessments:
                    if assessment.source_key in collected:
                        raise AIError(
                            f"AI returned duplicate assessment for {assessment.source_key}"
                        )
                    collected[assessment.source_key] = assessment

        expected = {issue.key for issue in issues}
        received = set(collected)
        if received != expected:
            missing = sorted(expected - received)
            extra = sorted(received - expected)
            raise AIError(
                f"AI assessment keys do not match input; missing={missing}, extra={extra}"
            )
        return [collected[issue.key] for issue in issues]

    def _analyze_batch(
        self,
        issues: list[JiraIssue],
        candidate_map: dict[str, list[JiraIssue]],
    ) -> list[IssueAssessment]:
        payload = {
            "today_utc": datetime.now(UTC).date().isoformat(),
            "product": {
                "name": self.product.name,
                "vision": self.product.vision,
                "personas": self.product.personas,
                "definition_of_done": self.product.definition_of_done,
                "constraints": self.product.constraints,
            },
            "issues": [
                {
                    "source": self._issue_payload(issue),
                    "possible_duplicate_or_dependency_candidates": [
                        {
                            "key": candidate.key,
                            "summary": candidate.summary,
                            "status": candidate.status_name,
                            "description_excerpt": candidate.description_text[:1000],
                        }
                        for candidate in candidate_map.get(issue.key, [])
                    ],
                }
                for issue in issues
            ],
        }
        try:
            response = self.client.responses.parse(
                model=self.config.model,
                reasoning={"effort": self.config.reasoning_effort},
                text={"verbosity": "medium"},
                store=False,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False, default=str),
                    },
                ],
                text_format=AssessmentBatch,
            )
        except Exception as exc:
            raise AIError(f"OpenAI request failed: {exc}") from exc

        parsed = response.output_parsed
        if parsed is None:
            raise AIError("OpenAI returned no parsed assessment")
        expected = {issue.key for issue in issues}
        received = {assessment.source_key for assessment in parsed.assessments}
        if expected != received:
            raise AIError(
                f"OpenAI batch keys do not match input; expected={sorted(expected)}, "
                f"received={sorted(received)}"
            )
        return parsed.assessments

    def _issue_payload(self, issue: JiraIssue) -> dict:
        description = ""
        if self.config.send_original_descriptions:
            description = issue.description_text[: self.config.max_description_characters]
        return {
            "key": issue.key,
            "summary": issue.summary,
            "description": description,
            "issue_type": issue.issue_type,
            "status": issue.status_name,
            "status_category": issue.status_category,
            "priority": issue.priority,
            "labels": issue.labels,
            "components": issue.components,
            "parent_key": issue.parent_key,
            "subtask_keys": issue.subtask_keys,
            "existing_linked_keys": issue.linked_keys,
            "created": issue.created.isoformat(),
            "updated": issue.updated.isoformat(),
        }
