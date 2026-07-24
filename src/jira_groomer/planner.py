from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta

from .adf import story_to_adf
from .config import Settings
from .models import (
    ArchiveIssueAction,
    CreateIssueAction,
    GroomingPlan,
    IssueAssessment,
    JiraIssue,
    LinkIssueAction,
    RankedIssue,
    RankIssuesAction,
    UpdateIssueAction,
)

STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "because",
    "before",
    "being",
    "but",
    "can",
    "could",
    "for",
    "from",
    "have",
    "into",
    "issue",
    "jira",
    "not",
    "our",
    "should",
    "that",
    "the",
    "their",
    "then",
    "there",
    "this",
    "ticket",
    "user",
    "want",
    "when",
    "where",
    "which",
    "with",
    "would",
}


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) >= 3 and token not in STOPWORDS
    }


def issue_similarity(left: JiraIssue, right: JiraIssue) -> float:
    left_summary = _tokens(left.summary)
    right_summary = _tokens(right.summary)
    left_all = _tokens(f"{left.summary} {left.description_text}")
    right_all = _tokens(f"{right.summary} {right.description_text}")

    def jaccard(a: set[str], b: set[str]) -> float:
        return len(a & b) / len(a | b) if a | b else 0.0

    return 0.65 * jaccard(left_summary, right_summary) + 0.35 * jaccard(left_all, right_all)


def candidate_similarity(left: JiraIssue, right: JiraIssue) -> float:
    """Broader recall score for possible duplicates, dependencies, and related work."""
    score = issue_similarity(left, right)
    if set(left.components) & set(right.components):
        score += 0.12
    if left.parent_key and left.parent_key == right.parent_key:
        score += 0.20
    if right.key in left.linked_keys or left.key in right.linked_keys:
        score += 0.30
    return min(score, 1.0)


def build_candidate_map(
    issues: list[JiraIssue],
    *,
    threshold: float,
    limit: int,
) -> dict[str, list[JiraIssue]]:
    scored: dict[str, list[tuple[float, JiraIssue]]] = {issue.key: [] for issue in issues}
    for index, left in enumerate(issues):
        for right in issues[index + 1 :]:
            similarity = candidate_similarity(left, right)
            if similarity >= threshold:
                scored[left.key].append((similarity, right))
                scored[right.key].append((similarity, left))
    return {
        key: [issue for _, issue in sorted(values, key=lambda item: item[0], reverse=True)[:limit]]
        for key, values in scored.items()
    }


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _action_id(kind: str, value: object) -> str:
    return f"{kind}-{_digest(value)[:16]}"


def source_fingerprint(issues: list[JiraIssue]) -> str:
    return _digest(
        [
            {
                "key": issue.key,
                "summary": issue.summary,
                "updated": issue.updated.isoformat(),
            }
            for issue in sorted(issues, key=lambda item: item.key)
        ]
    )


def _existing_links(issue: JiraIssue) -> set[tuple[str, str]]:
    links: set[tuple[str, str]] = set()
    for raw_link in issue.raw_fields.get("issuelinks", []):
        linked = raw_link.get("outwardIssue") or raw_link.get("inwardIssue") or {}
        key = linked.get("key")
        link_type = (raw_link.get("type") or {}).get("name")
        if key and link_type:
            links.add((str(key), str(link_type)))
    return links


def _score(
    assessment: IssueAssessment,
    settings: Settings,
    *,
    job_size: int | None = None,
) -> float:
    priority = assessment.priority
    numerator = (
        priority.business_value * settings.ranking.business_value_weight
        + priority.time_criticality * settings.ranking.time_criticality_weight
        + priority.risk_reduction * settings.ranking.risk_reduction_weight
    )
    denominator = max(
        job_size if job_size is not None else priority.job_size,
        settings.ranking.job_size_floor,
    )
    return round(numerator / denominator, 4)


def _rank_explanation(assessment: IssueAssessment) -> str:
    score = assessment.priority
    return (
        f"value={score.business_value}, time-criticality={score.time_criticality}, "
        f"risk-reduction={score.risk_reduction}, size={score.job_size}"
    )


def _eligible_for_archive(
    issue: JiraIssue,
    assessment: IssueAssessment,
    settings: Settings,
    now: datetime,
) -> tuple[bool, str]:
    if not settings.archive.enabled:
        return False, "archive planning is disabled"
    if issue.is_subtask:
        return False, "Jira cannot archive a subtask directly"
    if assessment.confidence < 0.9:
        return False, "AI confidence is below 0.90"
    if not assessment.archive_evidence:
        return False, "no concrete archive evidence was supplied"
    if issue.updated > now - timedelta(days=settings.archive.stale_after_days):
        return False, "issue is newer than the configured staleness threshold"
    status_allowed = (
        issue.status_category in settings.archive.allowed_status_categories
        or issue.status_name in settings.archive.allowed_status_names
    )
    if not status_allowed:
        return False, "status is not in the archive allow-list"
    return True, "archive evidence and deterministic policy both passed"


def build_plan(
    settings: Settings,
    issues: list[JiraIssue],
    assessments: list[IssueAssessment],
    *,
    config_sha256: str,
    now: datetime | None = None,
) -> GroomingPlan:
    now = now or datetime.now(UTC)
    issue_by_key = {issue.key: issue for issue in issues}
    assessment_by_key = {assessment.source_key: assessment for assessment in assessments}
    if set(issue_by_key) != set(assessment_by_key):
        raise ValueError("Every source issue must have exactly one assessment")

    creates: list[CreateIssueAction] = []
    updates: list[UpdateIssueAction] = []
    links: list[LinkIssueAction] = []
    archives: list[ArchiveIssueAction] = []
    warnings: list[str] = []
    ranked: list[RankedIssue] = []
    candidate_keys = set(issue_by_key)
    proposed_link_keys: set[tuple[str, str, str]] = set()
    candidate_map = build_candidate_map(
        issues,
        threshold=settings.quality.candidate_similarity_threshold,
        limit=settings.quality.duplicate_candidates_per_issue,
    )

    for issue in issues:
        assessment = assessment_by_key[issue.key]
        archive_eligible = False
        archive_detail = ""
        if assessment.recommendation == "archive_candidate":
            archive_eligible, archive_detail = _eligible_for_archive(
                issue, assessment, settings, now
            )
        if assessment.recommendation == "split":
            split_refs: list[str] = []
            split_count = max(len(assessment.split_stories), 1)
            split_size = max(1, math.ceil(assessment.priority.job_size / split_count))
            for index, split_story in enumerate(assessment.split_stories, start=1):
                if len(creates) >= settings.write_policy.max_creates_per_run:
                    warnings.append(
                        f"{issue.key}: additional split stories omitted by max_creates_per_run"
                    )
                    break
                temp_ref = f"NEW:{issue.key}:{index}"
                split_refs.append(temp_ref)
                create_value = {
                    "temp_ref": temp_ref,
                    "summary": split_story.title,
                    "source": issue.key,
                }
                create_action_id = _action_id("create", create_value)
                creates.append(
                    CreateIssueAction(
                        action_id=create_action_id,
                        temp_ref=temp_ref,
                        source_key=issue.key,
                        project_key=settings.jira.project_key,
                        issue_type=settings.jira.story_issue_type,
                        rationale=(
                            f"Vertical split proposed from {issue.key}: {assessment.rationale}"
                        ),
                        summary=split_story.title,
                        description=story_to_adf(split_story, preserve_original=False),
                        labels=sorted(
                            {
                                settings.quality.groomed_label,
                                f"split-from-{issue.key.lower()}",
                                f"groom-{create_action_id}",
                            }
                        ),
                        extra_fields=settings.jira.create_fields,
                    )
                )
                link_value = {
                    "outward": temp_ref,
                    "inward": issue.key,
                    "type": settings.links.related_link_type,
                }
                if len(links) < settings.write_policy.max_links_per_run:
                    links.append(
                        LinkIssueAction(
                            action_id=_action_id("link", link_value),
                            outward_ref=temp_ref,
                            inward_ref=issue.key,
                            link_type=settings.links.related_link_type,
                            rationale=f"Trace split story back to source {issue.key}",
                        )
                    )
                else:
                    warnings.append(f"{temp_ref}: source link omitted by max_links_per_run")
                ranked.append(
                    RankedIssue(
                        key_or_ref=temp_ref,
                        score=_score(assessment, settings, job_size=split_size),
                        explanation=(
                            f"Vertical split from {issue.key}; " + _rank_explanation(assessment)
                        ),
                    )
                )
            if not split_refs:
                warnings.append(
                    f"{issue.key}: split was recommended but no split story could be planned"
                )
            warnings.append(
                f"{issue.key}: source issue is retained for human disposition after vertical split"
            )
        elif assessment.recommendation != "archive_candidate" or not archive_eligible:
            ranked.append(
                RankedIssue(
                    key_or_ref=issue.key,
                    score=_score(assessment, settings),
                    explanation=_rank_explanation(assessment),
                )
            )

        should_rewrite = (
            assessment.recommendation == "rewrite"
            or (assessment.recommendation == "split" and not assessment.split_stories)
            or (
                assessment.recommendation == "keep"
                and assessment.invest.overall_quality < settings.quality.minimum_rewrite_score
            )
        )
        if should_rewrite:
            if len(updates) >= settings.write_policy.max_updates_per_run:
                warnings.append(f"{issue.key}: update omitted by max_updates_per_run")
            else:
                labels = set(issue.labels)
                labels.add(settings.quality.groomed_label)
                if assessment.story.open_questions or assessment.confidence < 0.8:
                    labels.add(settings.quality.needs_review_label)
                update_value = {
                    "key": issue.key,
                    "summary": assessment.story.title,
                    "updated": issue.updated.isoformat(),
                }
                updates.append(
                    UpdateIssueAction(
                        action_id=_action_id("update", update_value),
                        key=issue.key,
                        expected_updated=issue.updated,
                        rationale=assessment.rationale,
                        before_summary=issue.summary,
                        before_description=issue.raw_fields.get("description"),
                        before_labels=issue.labels,
                        summary=assessment.story.title,
                        description=story_to_adf(
                            assessment.story,
                            original_description=issue.description_text,
                            preserve_original=settings.quality.preserve_original_description,
                        ),
                        labels=sorted(labels),
                    )
                )

        allowed_targets = {candidate.key for candidate in candidate_map.get(issue.key, [])}
        for suggestion in assessment.link_suggestions:
            if suggestion.confidence < 0.85:
                continue
            if (
                suggestion.target_key not in candidate_keys
                or suggestion.target_key not in allowed_targets
            ):
                warnings.append(
                    f"{issue.key}: ignored link to non-candidate {suggestion.target_key}"
                )
                continue
            if suggestion.target_key == issue.key:
                continue
            if suggestion.relationship == "duplicate_of":
                if (
                    issue_similarity(issue, issue_by_key[suggestion.target_key])
                    < settings.quality.duplicate_similarity_threshold
                ):
                    warnings.append(
                        f"{issue.key}: ignored weak duplicate match to {suggestion.target_key}"
                    )
                    continue
                outward, inward, link_type = (
                    issue.key,
                    suggestion.target_key,
                    settings.links.duplicate_link_type,
                )
            elif suggestion.relationship == "blocks":
                outward, inward, link_type = (
                    issue.key,
                    suggestion.target_key,
                    settings.links.dependency_link_type,
                )
            elif suggestion.relationship == "is_blocked_by":
                outward, inward, link_type = (
                    suggestion.target_key,
                    issue.key,
                    settings.links.dependency_link_type,
                )
            else:
                outward, inward = sorted((issue.key, suggestion.target_key))
                link_type = settings.links.related_link_type

            dedupe_key = (outward, inward, link_type)
            if dedupe_key in proposed_link_keys:
                continue
            if (suggestion.target_key, link_type) in _existing_links(issue):
                continue
            if len(links) >= settings.write_policy.max_links_per_run:
                warnings.append("Additional link proposals omitted by max_links_per_run")
                break
            proposed_link_keys.add(dedupe_key)
            link_value = {"outward": outward, "inward": inward, "type": link_type}
            links.append(
                LinkIssueAction(
                    action_id=_action_id("link", link_value),
                    outward_ref=outward,
                    inward_ref=inward,
                    link_type=link_type,
                    rationale=suggestion.rationale,
                )
            )

        if assessment.recommendation == "archive_candidate":
            if archive_eligible and len(archives) < settings.archive.max_per_run:
                archive_value = {
                    "key": issue.key,
                    "updated": issue.updated.isoformat(),
                }
                archives.append(
                    ArchiveIssueAction(
                        action_id=_action_id("archive", archive_value),
                        key=issue.key,
                        expected_updated=issue.updated,
                        rationale=assessment.rationale,
                        evidence=assessment.archive_evidence,
                    )
                )
            else:
                detail = (
                    archive_detail
                    if not archive_eligible
                    else "archive proposal omitted by max_per_run"
                )
                warnings.append(f"{issue.key}: archive proposal not actionable: {detail}")

    ranked.sort(key=lambda item: (-item.score, item.key_or_ref))
    rank_actions: list[RankIssuesAction] = []
    if settings.ranking.enabled and settings.ranking.rank_anchor_issue and ranked:
        refs = [item.key_or_ref for item in ranked]
        if settings.ranking.rank_anchor_issue in refs:
            warnings.append("Ranking action omitted because rank_anchor_issue is in the ranked set")
        else:
            rank_value = {
                "refs": refs,
                "before": settings.ranking.rank_anchor_issue,
            }
            rank_actions.append(
                RankIssuesAction(
                    action_id=_action_id("rank", rank_value),
                    issue_refs=refs,
                    rank_before_issue=settings.ranking.rank_anchor_issue,
                    rationale="WSJF-style order from value, urgency, risk reduction, and size",
                )
            )
    elif settings.ranking.enabled and ranked:
        warnings.append(
            "Ranked order is reported only; set ranking.rank_anchor_issue to plan a Jira rank write"
        )

    actions = [*creates, *updates, *links, *rank_actions, *archives]
    fingerprint = source_fingerprint(issues)
    run_id = f"groom-{now.strftime('%Y%m%dT%H%M%SZ')}-{fingerprint[:8]}"
    return GroomingPlan(
        run_id=run_id,
        created_at=now,
        config_sha256=config_sha256,
        source_jql=settings.jira.jql,
        source_issue_count=len(issues),
        source_fingerprint=fingerprint,
        source_issues=issues,
        ranked_backlog=ranked,
        assessments=assessments,
        actions=actions,
        warnings=warnings,
    )
