from datetime import timedelta

from conftest import make_assessment, make_issue

from jira_groomer.apply import PlanApplier
from jira_groomer.config import planning_config_sha256
from jira_groomer.models import LinkSuggestion
from jira_groomer.planner import build_plan


class FakeJira:
    def __init__(self, issues):
        self.issues = {issue.key: issue for issue in issues}
        self.links: set[tuple[str, str, str]] = set()
        self.archived: list[str] = []

    def get_issue(self, key):
        return self.issues[key]

    def issue_has_link(self, outward, inward, link_type):
        return (outward, inward, link_type) in self.links

    def link_issues(self, *, outward_key, inward_key, link_type):
        self.links.add((outward_key, inward_key, link_type))
        for key in (outward_key, inward_key):
            issue = self.issues[key]
            self.issues[key] = issue.model_copy(
                update={"updated": issue.updated + timedelta(seconds=1)}
            )

    def archive_issues(self, issue_keys):
        self.archived.extend(issue_keys)
        return {"numberOfIssuesUpdated": len(issue_keys)}


def test_self_induced_link_timestamp_does_not_block_approved_archive(settings, tmp_path) -> None:
    first = make_issue(
        "WEB-1",
        summary="Customer resets forgotten password",
        status_name="Done",
        status_category="Done",
        age_days=500,
    )
    second = make_issue(
        "WEB-2",
        summary="Allow customer forgotten password reset",
        status_name="Done",
        status_category="Done",
        age_days=500,
    )
    archive = make_assessment(
        "WEB-1",
        recommendation="archive_candidate",
        archive_evidence=["WEB-2 explicitly supersedes this ticket"],
    )
    archive.link_suggestions = [
        LinkSuggestion(
            target_key="WEB-2",
            relationship="duplicate_of",
            rationale="The two issues describe the same customer outcome.",
            confidence=0.98,
        )
    ]
    keep = make_assessment("WEB-2", recommendation="keep", quality=95)
    keep.story.title = "Customer resets a forgotten password"
    settings.write_policy.allow_issue_links = True
    settings.write_policy.allow_archiving = True
    settings.archive.allow_apply = True
    plan = build_plan(
        settings,
        [first, second],
        [archive, keep],
        config_sha256=planning_config_sha256(settings),
    )
    jira = FakeJira([first, second])
    report = PlanApplier(settings, jira).apply(
        plan,
        state_path=tmp_path / "state.json",
        dry_run=False,
    )
    assert not [result for result in report.results if result.status == "failed"]
    assert jira.archived == ["WEB-1"]
