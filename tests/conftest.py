from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jira_groomer.config import Settings
from jira_groomer.models import (
    AcceptanceCriterion,
    CrossFunctionalNotes,
    InvestScore,
    IssueAssessment,
    JiraIssue,
    PrioritizationScore,
    StorySpec,
)


@pytest.fixture
def settings() -> Settings:
    return Settings.model_validate(
        {
            "jira": {
                "base_url": "https://example.atlassian.net",
                "jql": "project = WEB",
                "project_key": "WEB",
            },
            "product": {
                "name": "Web",
                "vision": "Make the customer journey excellent",
            },
            "archive": {
                "enabled": True,
                "allow_apply": False,
                "stale_after_days": 365,
                "allowed_status_categories": ["Done"],
                "allowed_status_names": ["Duplicate"],
            },
        }
    )


def make_issue(
    key: str = "WEB-1",
    *,
    summary: str = "bad ticket",
    description: str = "make checkout work",
    status_name: str = "To Do",
    status_category: str = "To Do",
    age_days: int = 10,
) -> JiraIssue:
    now = datetime.now(UTC)
    return JiraIssue(
        id=key.split("-")[-1],
        key=key,
        summary=summary,
        description_text=description,
        issue_type="Story",
        status_name=status_name,
        status_category=status_category,
        created=now - timedelta(days=age_days + 20),
        updated=now - timedelta(days=age_days),
        raw_fields={
            "description": {
                "version": 1,
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}],
                    }
                ],
            }
        },
    )


def make_story(title: str = "Customer completes checkout") -> StorySpec:
    return StorySpec(
        title=title,
        persona="customer",
        need="to complete checkout with a valid card",
        benefit="I can place my order",
        context="The current checkout does not make the result clear.",
        acceptance_criteria=[
            AcceptanceCriterion(
                given="a customer has a valid basket",
                when="they submit valid payment details",
                then="the order is confirmed exactly once",
            )
        ],
        non_functional_requirements=["Meet WCAG 2.2 AA for the payment flow"],
        cross_functional_notes=CrossFunctionalNotes(
            frontend=["Show a pending state"],
            backend=["Use an idempotency key"],
            shared=["Trace the transaction end to end"],
        ),
    )


def make_assessment(
    key: str = "WEB-1",
    *,
    recommendation: str = "rewrite",
    confidence: float = 0.95,
    quality: int = 30,
    archive_evidence: list[str] | None = None,
) -> IssueAssessment:
    return IssueAssessment(
        source_key=key,
        recommendation=recommendation,
        confidence=confidence,
        rationale="The source lacks a user outcome and testable behavior.",
        quality_findings=["No acceptance criteria"],
        invest=InvestScore(
            independent=3,
            negotiable=3,
            valuable=2,
            estimable=2,
            small=3,
            testable=1,
            overall_quality=quality,
        ),
        priority=PrioritizationScore(
            business_value=8,
            time_criticality=6,
            risk_reduction=4,
            job_size=3,
        ),
        story=make_story(),
        archive_evidence=archive_evidence or [],
    )
