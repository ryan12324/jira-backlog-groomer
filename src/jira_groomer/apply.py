from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import Settings, planning_config_sha256
from .errors import PolicyError
from .jira import JiraClient
from .models import (
    ActionResult,
    ApplyReport,
    ArchiveIssueAction,
    CreateIssueAction,
    GroomingPlan,
    JiraIssue,
    LinkIssueAction,
    RankIssuesAction,
    UpdateIssueAction,
)
from .planner import source_fingerprint


def _counts(plan: GroomingPlan) -> dict[str, int]:
    counts = {
        "update_issue": 0,
        "create_issue": 0,
        "link_issue": 0,
        "rank_issues": 0,
        "archive_issue": 0,
    }
    for action in plan.actions:
        counts[action.kind] += 1
    return counts


def validate_plan(plan: GroomingPlan, settings: Settings, *, enforce_write_gates: bool) -> None:
    if plan.config_sha256 != planning_config_sha256(settings):
        raise PolicyError(
            "The planning configuration changed after this plan was generated. "
            "Generate a new plan; write-gate changes are the only permitted exception."
        )
    if plan.source_jql != settings.jira.jql:
        raise PolicyError("Plan JQL does not match the current configuration")
    if plan.source_issue_count != len(plan.source_issues):
        raise PolicyError("Plan source_issue_count does not match its source snapshot")
    if plan.source_fingerprint != source_fingerprint(plan.source_issues):
        raise PolicyError("Plan source fingerprint does not match its source snapshot")
    now = datetime.now(UTC)
    if plan.created_at < now - timedelta(hours=settings.write_policy.plan_max_age_hours):
        raise PolicyError(
            f"Plan is older than {settings.write_policy.plan_max_age_hours} hours; regenerate it"
        )

    counts = _counts(plan)
    limits = {
        "update_issue": settings.write_policy.max_updates_per_run,
        "create_issue": settings.write_policy.max_creates_per_run,
        "link_issue": settings.write_policy.max_links_per_run,
        "archive_issue": settings.archive.max_per_run,
    }
    for kind, limit in limits.items():
        if counts[kind] > limit:
            raise PolicyError(
                f"Plan has {counts[kind]} {kind} actions; configured limit is {limit}"
            )

    if enforce_write_gates:
        gates = {
            "update_issue": settings.write_policy.allow_issue_updates,
            "create_issue": settings.write_policy.allow_issue_creation,
            "link_issue": settings.write_policy.allow_issue_links,
            "rank_issues": settings.write_policy.allow_ranking,
            "archive_issue": (
                settings.write_policy.allow_archiving and settings.archive.allow_apply
            ),
        }
        blocked = sorted(kind for kind, count in counts.items() if count and not gates[kind])
        if blocked:
            raise PolicyError(
                "Plan contains write types that are not enabled: " + ", ".join(blocked)
            )

    source_keys = {issue.key for issue in plan.source_issues}
    if len(source_keys) != len(plan.source_issues):
        raise PolicyError("Plan source snapshot contains duplicate issue keys")
    source_by_key = {issue.key: issue for issue in plan.source_issues}
    assessment_keys = {assessment.source_key for assessment in plan.assessments}
    if assessment_keys != source_keys or len(plan.assessments) != len(source_keys):
        raise PolicyError("Plan assessments do not match its source snapshot")
    create_actions = [action for action in plan.actions if isinstance(action, CreateIssueAction)]
    temp_refs = {action.temp_ref for action in create_actions}
    if len(temp_refs) != len(create_actions):
        raise PolicyError("Create actions must have unique temp_ref values")

    valid_refs = source_keys | temp_refs
    update_keys: set[str] = set()
    archive_keys: set[str] = set()
    link_keys: set[tuple[str, str, str]] = set()
    rank_action_seen = False
    created_so_far: set[str] = set()
    for action in plan.actions:
        if isinstance(action, CreateIssueAction):
            if action.project_key != settings.jira.project_key:
                raise PolicyError(f"{action.action_id} targets an unexpected project")
            if action.issue_type != settings.jira.story_issue_type:
                raise PolicyError(f"{action.action_id} uses an unexpected issue type")
            if action.extra_fields != settings.jira.create_fields:
                raise PolicyError(f"{action.action_id} has unexpected static create fields")
            if f"groom-{action.action_id}" not in action.labels:
                raise PolicyError(f"{action.action_id} is missing its idempotency label")
            if action.source_key not in source_keys:
                raise PolicyError(f"{action.action_id} has an unknown split source")
            created_so_far.add(action.temp_ref)
        elif isinstance(action, UpdateIssueAction):
            if action.key not in source_keys:
                raise PolicyError(f"{action.action_id} updates an issue outside the source set")
            if action.key in update_keys:
                raise PolicyError(f"Plan updates {action.key} more than once")
            source = source_by_key[action.key]
            if (
                action.expected_updated != source.updated
                or action.before_summary != source.summary
                or action.before_description != source.raw_fields.get("description")
                or action.before_labels != source.labels
            ):
                raise PolicyError(f"{action.action_id} before-values do not match the snapshot")
            if not set(source.labels) <= set(action.labels):
                raise PolicyError(f"{action.action_id} removes existing labels")
            update_keys.add(action.key)
        elif isinstance(action, ArchiveIssueAction):
            if action.key not in source_keys:
                raise PolicyError(f"{action.action_id} archives an issue outside the source set")
            if action.key in archive_keys:
                raise PolicyError(f"Plan archives {action.key} more than once")
            source = source_by_key[action.key]
            assessment = next(item for item in plan.assessments if item.source_key == action.key)
            if action.expected_updated != source.updated:
                raise PolicyError(f"{action.action_id} timestamp does not match the snapshot")
            if action.evidence != assessment.archive_evidence:
                raise PolicyError(f"{action.action_id} evidence does not match its assessment")
            archive_keys.add(action.key)
        elif isinstance(action, LinkIssueAction):
            if action.outward_ref not in valid_refs or action.inward_ref not in valid_refs:
                raise PolicyError(f"{action.action_id} contains an unknown issue reference")
            if action.outward_ref == action.inward_ref:
                raise PolicyError(f"{action.action_id} links an issue to itself")
            for ref in (action.outward_ref, action.inward_ref):
                if ref.startswith("NEW:") and ref not in created_so_far:
                    raise PolicyError(f"{action.action_id} uses {ref} before it is created")
            allowed_link_types = {
                settings.links.duplicate_link_type,
                settings.links.dependency_link_type,
                settings.links.related_link_type,
            }
            if action.link_type not in allowed_link_types:
                raise PolicyError(f"{action.action_id} uses an unexpected link type")
            link_key = (action.outward_ref, action.inward_ref, action.link_type)
            if link_key in link_keys:
                raise PolicyError(f"Plan contains duplicate link action {link_key}")
            link_keys.add(link_key)
        elif isinstance(action, RankIssuesAction):
            if rank_action_seen:
                raise PolicyError("Plan contains more than one rank action")
            rank_action_seen = True
            if action.rank_before_issue != settings.ranking.rank_anchor_issue:
                raise PolicyError(f"{action.action_id} has an unexpected rank anchor")
            if not set(action.issue_refs) <= valid_refs:
                raise PolicyError(f"{action.action_id} contains an unknown issue reference")
            if len(action.issue_refs) != len(set(action.issue_refs)):
                raise PolicyError(f"{action.action_id} contains duplicate issue references")
            for ref in action.issue_refs:
                if ref.startswith("NEW:") and ref not in created_so_far:
                    raise PolicyError(f"{action.action_id} uses {ref} before it is created")
    overlap = update_keys & archive_keys
    if overlap:
        raise PolicyError(f"Plan both updates and archives the same issues: {sorted(overlap)}")


class PlanApplier:
    def __init__(self, settings: Settings, jira: JiraClient | None) -> None:
        self.settings = settings
        self.jira = jira

    def apply(
        self,
        plan: GroomingPlan,
        *,
        state_path: Path,
        dry_run: bool,
    ) -> ApplyReport:
        validate_plan(plan, self.settings, enforce_write_gates=not dry_run)
        if dry_run:
            return ApplyReport(
                run_id=plan.run_id,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                dry_run=True,
                results=[
                    ActionResult(
                        action_id=action.action_id,
                        kind=action.kind,
                        status="skipped",
                        detail="Dry run: policy and references validated; no Jira request made",
                    )
                    for action in plan.actions
                ],
            )
        if self.jira is None:
            raise PolicyError("A Jira client is required for a real apply")

        report = self._load_or_initialize_report(plan, state_path)
        latest = {result.action_id: result for result in report.results}
        refs = {
            result.detail.split("=", 1)[0]: result.created_key
            for result in report.results
            if result.created_key and result.detail.startswith("NEW:")
        }
        known_updated = {
            issue.key: issue.updated for issue in plan.source_issues
        } | report.observed_updated

        for action in plan.actions:
            prior = latest.get(action.action_id)
            if prior and prior.status == "applied":
                if prior.created_key and isinstance(action, CreateIssueAction):
                    refs[action.temp_ref] = prior.created_key
                continue
            try:
                result = self._apply_action(plan, action, refs, known_updated)
            except Exception as exc:
                result = ActionResult(
                    action_id=action.action_id,
                    kind=action.kind,
                    status="failed",
                    detail=str(exc),
                )
                report.results.append(result)
                report.observed_updated = known_updated
                report.finished_at = datetime.now(UTC)
                self._write_report(report, state_path)
                break
            report.results.append(result)
            latest[action.action_id] = result
            if result.created_key and isinstance(action, CreateIssueAction):
                refs[action.temp_ref] = result.created_key
            report.observed_updated = known_updated
            self._write_report(report, state_path)
        else:
            report.finished_at = datetime.now(UTC)
            self._write_report(report, state_path)
        return report

    def _apply_action(
        self,
        plan: GroomingPlan,
        action: object,
        refs: dict[str, str],
        known_updated: dict[str, datetime],
    ) -> ActionResult:
        if isinstance(action, CreateIssueAction):
            idempotency_label = f"groom-{action.action_id}"
            existing = self.jira.find_issue_by_label(action.project_key, idempotency_label)
            if existing:
                if existing.summary != action.summary:
                    raise PolicyError(
                        f"Idempotency label {idempotency_label} exists on a different story"
                    )
                return ActionResult(
                    action_id=action.action_id,
                    kind=action.kind,
                    status="applied",
                    detail=f"{action.temp_ref}=existing",
                    created_key=existing.key,
                )
            key = self.jira.create_issue(
                project_key=action.project_key,
                issue_type=action.issue_type,
                summary=action.summary,
                description=action.description,
                labels=action.labels,
                extra_fields=action.extra_fields,
            )
            return ActionResult(
                action_id=action.action_id,
                kind=action.kind,
                status="applied",
                detail=f"{action.temp_ref}=created",
                created_key=key,
            )

        if isinstance(action, UpdateIssueAction):
            current = self.jira.get_issue(action.key)
            already_matches = (
                current.summary == action.summary
                and current.raw_fields.get("description") == action.description
                and set(current.labels) == set(action.labels)
            )
            if already_matches:
                known_updated[action.key] = current.updated
                return ActionResult(
                    action_id=action.action_id,
                    kind=action.kind,
                    status="applied",
                    detail=f"{action.key} already matches the approved plan",
                )
            self._check_unchanged(action.key, current.updated, action.expected_updated)
            self.jira.update_issue(
                action.key,
                summary=action.summary,
                description=action.description,
                labels=action.labels,
            )
            known_updated[action.key] = self.jira.get_issue(action.key).updated
            return ActionResult(
                action_id=action.action_id,
                kind=action.kind,
                status="applied",
                detail=f"Updated {action.key}",
            )

        if isinstance(action, LinkIssueAction):
            outward = self._resolve_ref(action.outward_ref, refs)
            inward = self._resolve_ref(action.inward_ref, refs)
            if self.jira.issue_has_link(outward, inward, action.link_type):
                detail = f"{outward} already has {action.link_type} link to {inward}"
            else:
                for key in (outward, inward):
                    if key in known_updated:
                        current = self.jira.get_issue(key)
                        self._check_unchanged(
                            key,
                            current.updated,
                            known_updated[key],
                        )
                self.jira.link_issues(
                    outward_key=outward,
                    inward_key=inward,
                    link_type=action.link_type,
                )
                detail = f"Linked {outward} to {inward} as {action.link_type}"
            for key in (outward, inward):
                if key in known_updated:
                    known_updated[key] = self.jira.get_issue(key).updated
            return ActionResult(
                action_id=action.action_id,
                kind=action.kind,
                status="applied",
                detail=detail,
            )

        if isinstance(action, RankIssuesAction):
            keys = [self._resolve_ref(ref, refs) for ref in action.issue_refs]
            self.jira.rank_issues(keys, action.rank_before_issue)
            return ActionResult(
                action_id=action.action_id,
                kind=action.kind,
                status="applied",
                detail=f"Ranked {len(keys)} issues before {action.rank_before_issue}",
            )

        if isinstance(action, ArchiveIssueAction):
            current = self.jira.get_issue(action.key)
            self._check_unchanged(
                action.key,
                current.updated,
                known_updated.get(action.key, action.expected_updated),
            )
            self._check_archive_policy(plan, action, current)
            result = self.jira.archive_issues([action.key])
            if int(result.get("numberOfIssuesUpdated", 0)) < 1:
                raise PolicyError(f"Jira did not confirm that {action.key} was archived")
            return ActionResult(
                action_id=action.action_id,
                kind=action.kind,
                status="applied",
                detail=f"Archived {action.key}",
            )

        raise PolicyError(f"Unsupported action type: {type(action).__name__}")

    def _check_unchanged(
        self,
        key: str,
        current_updated: datetime,
        expected_updated: datetime,
    ) -> None:
        if (
            self.settings.write_policy.require_unchanged_issues
            and current_updated != expected_updated
        ):
            raise PolicyError(
                f"{key} changed after planning (expected {expected_updated.isoformat()}, "
                f"found {current_updated.isoformat()}); regenerate the plan"
            )

    def _check_archive_policy(
        self,
        plan: GroomingPlan,
        action: ArchiveIssueAction,
        current: JiraIssue,
    ) -> None:
        assessment = next(item for item in plan.assessments if item.source_key == action.key)
        source = next(item for item in plan.source_issues if item.key == action.key)
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.archive.stale_after_days)
        status_allowed = (
            current.status_category in self.settings.archive.allowed_status_categories
            or current.status_name in self.settings.archive.allowed_status_names
        )
        if (
            assessment.recommendation != "archive_candidate"
            or assessment.confidence < 0.9
            or not assessment.archive_evidence
            or source.updated > cutoff
            or not status_allowed
            or current.is_subtask
        ):
            raise PolicyError(f"{action.key} no longer satisfies the archive policy")

    @staticmethod
    def _resolve_ref(ref: str, refs: dict[str, str]) -> str:
        if not ref.startswith("NEW:"):
            return ref
        try:
            return refs[ref]
        except KeyError as exc:
            raise PolicyError(f"Created issue reference {ref} has not been resolved") from exc

    @staticmethod
    def _load_or_initialize_report(plan: GroomingPlan, path: Path) -> ApplyReport:
        if not path.exists():
            return ApplyReport(
                run_id=plan.run_id,
                started_at=datetime.now(UTC),
                dry_run=False,
            )
        try:
            report = ApplyReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PolicyError(f"Cannot read apply state {path}: {exc}") from exc
        if report.run_id != plan.run_id or report.dry_run:
            raise PolicyError(f"Apply state {path} belongs to a different run")
        report.finished_at = None
        return report

    @staticmethod
    def _write_report(report: ApplyReport, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
