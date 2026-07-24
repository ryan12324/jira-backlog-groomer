from datetime import UTC, datetime

import pytest
from conftest import make_assessment, make_issue

from jira_groomer.apply import validate_plan
from jira_groomer.config import planning_config_sha256
from jira_groomer.errors import PolicyError
from jira_groomer.planner import build_plan


def test_write_gate_must_be_enabled_before_apply(settings) -> None:
    issue = make_issue()
    plan = build_plan(
        settings,
        [issue],
        [make_assessment()],
        config_sha256=planning_config_sha256(settings),
        now=datetime.now(UTC),
    )
    validate_plan(plan, settings, enforce_write_gates=False)
    with pytest.raises(PolicyError, match="not enabled"):
        validate_plan(plan, settings, enforce_write_gates=True)

    settings.write_policy.allow_issue_updates = True
    validate_plan(plan, settings, enforce_write_gates=True)


def test_semantic_config_change_invalidates_plan(settings) -> None:
    issue = make_issue()
    plan = build_plan(
        settings,
        [issue],
        [make_assessment()],
        config_sha256=planning_config_sha256(settings),
        now=datetime.now(UTC),
    )
    settings.jira.project_key = "OTHER"
    with pytest.raises(PolicyError, match="configuration changed"):
        validate_plan(plan, settings, enforce_write_gates=False)
