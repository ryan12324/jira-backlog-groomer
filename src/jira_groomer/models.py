from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AcceptanceCriterion(StrictModel):
    given: str
    when: str
    then: str


class CrossFunctionalNotes(StrictModel):
    frontend: list[str] = Field(default_factory=list)
    backend: list[str] = Field(default_factory=list)
    shared: list[str] = Field(default_factory=list)


class StorySpec(StrictModel):
    title: str = Field(min_length=5, max_length=255)
    persona: str
    need: str
    benefit: str
    context: str
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1, max_length=12)
    non_functional_requirements: list[str] = Field(default_factory=list, max_length=12)
    dependencies: list[str] = Field(default_factory=list, max_length=12)
    out_of_scope: list[str] = Field(default_factory=list, max_length=12)
    open_questions: list[str] = Field(default_factory=list, max_length=12)
    cross_functional_notes: CrossFunctionalNotes = Field(default_factory=CrossFunctionalNotes)


class InvestScore(StrictModel):
    independent: int = Field(ge=1, le=5)
    negotiable: int = Field(ge=1, le=5)
    valuable: int = Field(ge=1, le=5)
    estimable: int = Field(ge=1, le=5)
    small: int = Field(ge=1, le=5)
    testable: int = Field(ge=1, le=5)
    overall_quality: int = Field(ge=0, le=100)


class PrioritizationScore(StrictModel):
    business_value: int = Field(ge=1, le=10)
    time_criticality: int = Field(ge=1, le=10)
    risk_reduction: int = Field(ge=1, le=10)
    job_size: int = Field(ge=1, le=10)


class LinkSuggestion(StrictModel):
    target_key: str
    relationship: Literal["duplicate_of", "blocks", "is_blocked_by", "relates_to"]
    rationale: str
    confidence: float = Field(ge=0, le=1)


class IssueAssessment(StrictModel):
    source_key: str
    recommendation: Literal["keep", "rewrite", "split", "archive_candidate"]
    confidence: float = Field(ge=0, le=1)
    rationale: str
    quality_findings: list[str] = Field(default_factory=list, max_length=12)
    invest: InvestScore
    priority: PrioritizationScore
    story: StorySpec
    split_stories: list[StorySpec] = Field(default_factory=list, max_length=8)
    link_suggestions: list[LinkSuggestion] = Field(default_factory=list, max_length=12)
    archive_evidence: list[str] = Field(default_factory=list, max_length=8)


class AssessmentBatch(StrictModel):
    assessments: list[IssueAssessment]


class JiraIssue(StrictModel):
    id: str
    key: str
    summary: str
    description_text: str = ""
    issue_type: str = ""
    is_subtask: bool = False
    status_name: str = ""
    status_category: str = ""
    priority: str | None = None
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    parent_key: str | None = None
    subtask_keys: list[str] = Field(default_factory=list)
    linked_keys: list[str] = Field(default_factory=list)
    created: datetime
    updated: datetime
    raw_fields: dict = Field(default_factory=dict)


class UpdateIssueAction(StrictModel):
    kind: Literal["update_issue"] = "update_issue"
    action_id: str
    key: str
    expected_updated: datetime
    rationale: str
    before_summary: str
    before_description: object | None = None
    before_labels: list[str] = Field(default_factory=list)
    summary: str
    description: dict
    labels: list[str]


class CreateIssueAction(StrictModel):
    kind: Literal["create_issue"] = "create_issue"
    action_id: str
    temp_ref: str
    source_key: str
    project_key: str
    issue_type: str
    rationale: str
    summary: str
    description: dict
    labels: list[str]
    extra_fields: dict = Field(default_factory=dict)


class LinkIssueAction(StrictModel):
    kind: Literal["link_issue"] = "link_issue"
    action_id: str
    outward_ref: str
    inward_ref: str
    link_type: str
    rationale: str


class ArchiveIssueAction(StrictModel):
    kind: Literal["archive_issue"] = "archive_issue"
    action_id: str
    key: str
    expected_updated: datetime
    rationale: str
    evidence: list[str]


class RankIssuesAction(StrictModel):
    kind: Literal["rank_issues"] = "rank_issues"
    action_id: str
    issue_refs: list[str]
    rank_before_issue: str
    rationale: str


PlanAction = Annotated[
    UpdateIssueAction | CreateIssueAction | LinkIssueAction | ArchiveIssueAction | RankIssuesAction,
    Field(discriminator="kind"),
]


class RankedIssue(StrictModel):
    key_or_ref: str
    score: float
    explanation: str


class GroomingPlan(StrictModel):
    schema_version: Literal["1"] = "1"
    run_id: str
    created_at: datetime
    config_sha256: str
    source_jql: str
    source_issue_count: int
    source_fingerprint: str
    source_issues: list[JiraIssue]
    ranked_backlog: list[RankedIssue]
    assessments: list[IssueAssessment]
    actions: list[PlanAction]
    warnings: list[str] = Field(default_factory=list)

    @field_validator("actions")
    @classmethod
    def unique_action_ids(cls, value: list[PlanAction]) -> list[PlanAction]:
        ids = [action.action_id for action in value]
        if len(ids) != len(set(ids)):
            raise ValueError("action_id values must be unique")
        return value


class ActionResult(StrictModel):
    action_id: str
    kind: str
    status: Literal["applied", "skipped", "failed"]
    detail: str
    created_key: str | None = None


class ApplyReport(StrictModel):
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    dry_run: bool
    results: list[ActionResult] = Field(default_factory=list)
    observed_updated: dict[str, datetime] = Field(default_factory=dict)
