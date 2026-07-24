from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

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
- For an Epic, treat it as an outcome container. Normally recommend split and produce small
  vertical stories; never split by technical layer.
- For a Bug, set story.delivery_kind to bug_fix. Preserve the defect rather than inventing a new
  feature, and populate observed behavior, expected behavior, reproduction steps, environment
  when known, user impact, and regression-focused acceptance criteria.
- For a Sub-task that is only a frontend/backend implementation fragment, recommend
  merge_candidate so a human can fold it into its parent vertical story. Do not manufacture an
  independent user outcome. Rewrite only if the sub-task has independently testable value.
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
        *,
        cache_dir: Path | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[IssueAssessment]:
        batches = [
            issues[start : start + self.config.batch_size]
            for start in range(0, len(issues), self.config.batch_size)
        ]
        if not batches:
            return []
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

        collected: dict[str, IssueAssessment] = {}
        completed = 0
        with ThreadPoolExecutor(max_workers=self.config.max_parallel_requests) as executor:
            futures = {
                executor.submit(
                    self._analyze_batch,
                    batch,
                    candidate_map,
                    cache_dir,
                ): batch
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
                completed += 1
                if progress:
                    progress(completed, len(batches))

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
        cache_dir: Path | None,
    ) -> list[IssueAssessment]:
        payload = self._batch_payload(issues, candidate_map)
        cache_path = self._cache_path(payload, cache_dir)
        if cache_path and cache_path.exists():
            try:
                cached = AssessmentBatch.model_validate_json(cache_path.read_text(encoding="utf-8"))
                self._validate_batch_keys(issues, cached)
                return cached.assessments
            except (OSError, ValueError, AIError):
                pass

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
        self._validate_batch_keys(issues, parsed)
        if cache_path:
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_text(parsed.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(cache_path)
        return parsed.assessments

    def _batch_payload(
        self,
        issues: list[JiraIssue],
        candidate_map: dict[str, list[JiraIssue]],
    ) -> dict:
        return {
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

    def _cache_path(self, payload: dict, cache_dir: Path | None) -> Path | None:
        if not cache_dir:
            return None
        cache_value = {
            "model": self.config.model,
            "reasoning_effort": self.config.reasoning_effort,
            "system_prompt": SYSTEM_PROMPT,
            "payload": payload,
        }
        encoded = json.dumps(
            cache_value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode()
        return cache_dir / f"{hashlib.sha256(encoded).hexdigest()}.json"

    @staticmethod
    def _validate_batch_keys(
        issues: list[JiraIssue],
        parsed: AssessmentBatch,
    ) -> None:
        expected = {issue.key for issue in issues}
        received = {assessment.source_key for assessment in parsed.assessments}
        if expected != received or len(parsed.assessments) != len(issues):
            raise AIError(
                f"OpenAI batch keys do not match input; expected={sorted(expected)}, "
                f"received={sorted(received)}"
            )

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
