from __future__ import annotations

import hashlib
import json
import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from .errors import ConfigurationError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JiraConfig(StrictModel):
    base_url: str | None = None
    auth_mode: Literal["basic", "bearer"] = "basic"
    jql: str
    project_key: str
    story_issue_type: str = "Story"
    create_fields: dict[str, Any] = Field(default_factory=dict)
    fields: list[str] = Field(
        default_factory=lambda: [
            "summary",
            "description",
            "issuetype",
            "status",
            "priority",
            "labels",
            "components",
            "parent",
            "subtasks",
            "issuelinks",
            "created",
            "updated",
        ]
    )


class AIConfig(StrictModel):
    model: str = "gpt-5.6-sol"
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "medium"
    batch_size: int = Field(default=10, ge=1, le=25)
    max_parallel_requests: int = Field(default=4, ge=1, le=16)
    max_description_characters: int = Field(default=12_000, ge=1000, le=100_000)
    send_original_descriptions: bool = True


class ProductConfig(StrictModel):
    name: str
    vision: str
    personas: list[str] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class QualityConfig(StrictModel):
    minimum_rewrite_score: int = Field(default=80, ge=0, le=100)
    candidate_similarity_threshold: float = Field(default=0.08, ge=0, le=1)
    duplicate_similarity_threshold: float = Field(default=0.28, ge=0, le=1)
    duplicate_candidates_per_issue: int = Field(default=5, ge=0, le=20)
    preserve_original_description: bool = True
    groomed_label: str = "ai-groomed"
    needs_review_label: str = "ai-needs-product-review"


class RankingConfig(StrictModel):
    enabled: bool = True
    business_value_weight: float = Field(default=1.0, ge=0)
    time_criticality_weight: float = Field(default=1.0, ge=0)
    risk_reduction_weight: float = Field(default=1.0, ge=0)
    job_size_floor: int = Field(default=1, ge=1, le=10)
    rank_anchor_issue: str | None = None


class ArchiveConfig(StrictModel):
    enabled: bool = True
    allow_apply: bool = False
    stale_after_days: int = Field(default=365, ge=1)
    allowed_status_categories: list[str] = Field(default_factory=lambda: ["Done"])
    allowed_status_names: list[str] = Field(
        default_factory=lambda: ["Duplicate", "Won't Do", "Obsolete"]
    )
    max_per_run: int = Field(default=20, ge=0, le=1000)


class WritePolicyConfig(StrictModel):
    allow_issue_updates: bool = False
    allow_issue_creation: bool = False
    allow_issue_links: bool = False
    allow_ranking: bool = False
    allow_archiving: bool = False
    max_updates_per_run: int = Field(default=100, ge=0, le=10_000)
    max_creates_per_run: int = Field(default=30, ge=0, le=1000)
    max_links_per_run: int = Field(default=100, ge=0, le=10_000)
    require_unchanged_issues: bool = True
    plan_max_age_hours: int = Field(default=72, ge=1, le=720)


class LinksConfig(StrictModel):
    duplicate_link_type: str = "Duplicate"
    dependency_link_type: str = "Blocks"
    related_link_type: str = "Relates"


class Settings(StrictModel):
    jira: JiraConfig
    ai: AIConfig = Field(default_factory=AIConfig)
    product: ProductConfig
    quality: QualityConfig = Field(default_factory=QualityConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    archive: ArchiveConfig = Field(default_factory=ArchiveConfig)
    write_policy: WritePolicyConfig = Field(default_factory=WritePolicyConfig)
    links: LinksConfig = Field(default_factory=LinksConfig)

    @model_validator(mode="after")
    def validate_safety_contract(self) -> Settings:
        if self.write_policy.allow_archiving and not self.archive.allow_apply:
            raise ValueError(
                "write_policy.allow_archiving requires archive.allow_apply=true as a second gate"
            )
        reserved_create_fields = {
            "project",
            "issuetype",
            "summary",
            "description",
            "labels",
        } & set(self.jira.create_fields)
        if reserved_create_fields:
            raise ValueError(
                "jira.create_fields cannot override managed fields: "
                + ", ".join(sorted(reserved_create_fields))
            )
        required_fields = {"summary", "description", "issuetype", "status", "created", "updated"}
        missing_fields = required_fields - set(self.jira.fields)
        if missing_fields:
            raise ValueError(
                "jira.fields is missing required fields: " + ", ".join(sorted(missing_fields))
            )
        return self

    @classmethod
    def load(cls, path: str | Path) -> Settings:
        config_path = Path(path)
        try:
            raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
            settings = cls.model_validate(raw)
        except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
            raise ConfigurationError(f"Cannot load {config_path}: {exc}") from exc

        env_base_url = os.getenv("JIRA_BASE_URL")
        if env_base_url:
            settings.jira.base_url = env_base_url
        if not settings.jira.base_url:
            raise ConfigurationError(
                "Jira base URL is required in the config or JIRA_BASE_URL environment variable"
            )
        settings.jira.base_url = settings.jira.base_url.rstrip("/")
        if not settings.jira.base_url.startswith("https://"):
            raise ConfigurationError("Jira base URL must use HTTPS")
        return settings


class JiraCredentials(StrictModel):
    auth_mode: Literal["basic", "bearer"]
    email: str | None = None
    api_token: SecretStr | None = None
    bearer_token: SecretStr | None = None

    @model_validator(mode="after")
    def validate_auth(self) -> JiraCredentials:
        if self.auth_mode == "basic" and (not self.email or not self.api_token):
            raise ValueError("Basic auth requires JIRA_EMAIL and JIRA_API_TOKEN")
        if self.auth_mode == "bearer" and not self.bearer_token:
            raise ValueError("Bearer auth requires JIRA_BEARER_TOKEN")
        return self

    @classmethod
    def from_environment(cls, default_mode: str) -> JiraCredentials:
        mode = os.getenv("JIRA_AUTH_MODE", default_mode)
        try:
            return cls(
                auth_mode=mode,
                email=os.getenv("JIRA_EMAIL"),
                api_token=os.getenv("JIRA_API_TOKEN"),
                bearer_token=os.getenv("JIRA_BEARER_TOKEN"),
            )
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc


def planning_config_sha256(settings: Settings) -> str:
    """Hash planning semantics while allowing write gates to be enabled after review."""
    payload = settings.model_dump(mode="json")
    payload["archive"].pop("allow_apply", None)
    for key in [
        "allow_issue_updates",
        "allow_issue_creation",
        "allow_issue_links",
        "allow_ranking",
        "allow_archiving",
    ]:
        payload["write_policy"].pop(key, None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
