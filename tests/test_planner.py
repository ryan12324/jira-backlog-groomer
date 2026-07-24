from datetime import UTC, datetime

from conftest import make_assessment, make_issue

from jira_groomer.models import ArchiveIssueAction, UpdateIssueAction
from jira_groomer.planner import build_candidate_map, build_plan, issue_similarity


def test_similarity_builds_bounded_candidates() -> None:
    one = make_issue("WEB-1", summary="Customer resets forgotten password")
    two = make_issue("WEB-2", summary="Allow customer password reset")
    three = make_issue("WEB-3", summary="Export finance ledger")
    assert issue_similarity(one, two) > issue_similarity(one, three)
    candidates = build_candidate_map([one, two, three], threshold=0.1, limit=1)
    assert candidates["WEB-1"][0].key == "WEB-2"


def test_rewrite_action_preserves_source_and_adds_label(settings) -> None:
    issue = make_issue()
    assessment = make_assessment()
    plan = build_plan(
        settings,
        [issue],
        [assessment],
        config_sha256="abc",
        now=datetime.now(UTC),
    )
    action = next(item for item in plan.actions if isinstance(item, UpdateIssueAction))
    assert action.before_summary == "bad ticket"
    assert action.summary == "Customer completes checkout"
    assert "ai-groomed" in action.labels
    assert plan.source_issues[0].key == issue.key


def test_archive_requires_evidence_staleness_and_allowed_status(settings) -> None:
    issue = make_issue(
        status_name="Done",
        status_category="Done",
        age_days=500,
    )
    assessment = make_assessment(
        recommendation="archive_candidate",
        archive_evidence=["A later issue is explicitly identified as the replacement"],
    )
    plan = build_plan(
        settings,
        [issue],
        [assessment],
        config_sha256="abc",
        now=datetime.now(UTC),
    )
    assert any(isinstance(action, ArchiveIssueAction) for action in plan.actions)


def test_recent_archive_candidate_stays_in_ranked_backlog(settings) -> None:
    issue = make_issue(
        status_name="Done",
        status_category="Done",
        age_days=20,
    )
    assessment = make_assessment(
        recommendation="archive_candidate",
        archive_evidence=["Superseded"],
    )
    plan = build_plan(
        settings,
        [issue],
        [assessment],
        config_sha256="abc",
        now=datetime.now(UTC),
    )
    assert not any(isinstance(action, ArchiveIssueAction) for action in plan.actions)
    assert plan.ranked_backlog[0].key_or_ref == issue.key


def test_subtask_merge_candidate_is_not_top_level_ranked(settings) -> None:
    issue = make_issue("WEB-2")
    issue.is_subtask = True
    issue.parent_key = "WEB-1"
    assessment = make_assessment("WEB-2", recommendation="merge_candidate", quality=20)
    plan = build_plan(
        settings,
        [issue],
        [assessment],
        config_sha256="abc",
        now=datetime.now(UTC),
    )
    assert plan.ranked_backlog == []
    assert any("merge candidate" in warning for warning in plan.warnings)
