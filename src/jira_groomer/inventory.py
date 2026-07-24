from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from .models import BacklogInventory, InventoryWave, JiraIssue


def _age_bucket(issue: JiraIssue, now: datetime) -> str:
    days = max(0, (now - issue.updated).days)
    if days <= 30:
        return "0-30 days"
    if days <= 90:
        return "31-90 days"
    if days <= 180:
        return "91-180 days"
    if days <= 365:
        return "181-365 days"
    return "366+ days"


def _parent_group(issue: JiraIssue) -> str:
    return issue.parent_key or issue.key


def build_inventory(
    issues: list[JiraIssue],
    *,
    source_jql: str,
    wave_size: int,
    now: datetime | None = None,
) -> BacklogInventory:
    if wave_size < 1:
        raise ValueError("wave_size must be positive")
    now = now or datetime.now(UTC)
    key_set = {issue.key for issue in issues}
    groups: dict[str, list[JiraIssue]] = {}
    group_order: list[str] = []
    for issue in issues:
        group = _parent_group(issue)
        if group not in groups:
            groups[group] = []
            group_order.append(group)
        groups[group].append(issue)

    oversized = {
        group: len(group_issues)
        for group, group_issues in groups.items()
        if len(group_issues) > wave_size
    }
    packed: list[tuple[list[JiraIssue], list[str]]] = []
    current_issues: list[JiraIssue] = []
    current_groups: list[str] = []

    def flush() -> None:
        nonlocal current_issues, current_groups
        if current_issues:
            packed.append((current_issues, current_groups))
            current_issues = []
            current_groups = []

    for group in group_order:
        group_issues = groups[group]
        if len(group_issues) > wave_size:
            flush()
            for start in range(0, len(group_issues), wave_size):
                packed.append((group_issues[start : start + wave_size], [group]))
            continue
        if current_issues and len(current_issues) + len(group_issues) > wave_size:
            flush()
        current_issues.extend(group_issues)
        current_groups.append(group)
    flush()

    waves = [
        InventoryWave(
            wave_number=index,
            keys=[issue.key for issue in wave_issues],
            issue_type_counts=dict(
                sorted(Counter(issue.issue_type or "Unknown" for issue in wave_issues).items())
            ),
            parent_groups=parent_groups,
        )
        for index, (wave_issues, parent_groups) in enumerate(packed, start=1)
    ]
    return BacklogInventory(
        created_at=now,
        source_jql=source_jql,
        issue_count=len(issues),
        issue_type_counts=dict(
            sorted(Counter(issue.issue_type or "Unknown" for issue in issues).items())
        ),
        status_counts=dict(
            sorted(Counter(issue.status_name or "Unknown" for issue in issues).items())
        ),
        status_category_counts=dict(
            sorted(Counter(issue.status_category or "Unknown" for issue in issues).items())
        ),
        age_bucket_counts=dict(
            sorted(Counter(_age_bucket(issue, now) for issue in issues).items())
        ),
        orphan_subtask_keys=sorted(
            issue.key
            for issue in issues
            if issue.is_subtask and (not issue.parent_key or issue.parent_key not in key_set)
        ),
        oversized_parent_groups=oversized,
        waves=waves,
    )


def inventory_to_markdown(inventory: BacklogInventory) -> str:
    lines = [
        "# Jira backlog inventory",
        "",
        f"- Created: {inventory.created_at.isoformat()}",
        f"- Source JQL: `{inventory.source_jql}`",
        f"- Issues: {inventory.issue_count}",
        f"- Proposed grooming waves: {len(inventory.waves)}",
        "- Status: read-only inventory; no AI analysis or Jira writes occurred.",
        "",
    ]

    def table(title: str, values: dict[str, int]) -> None:
        lines.extend([f"## {title}", "", "| Value | Count |", "|---|---:|"])
        lines.extend(f"| {key} | {count} |" for key, count in values.items())
        lines.append("")

    table("Issue types", inventory.issue_type_counts)
    table("Statuses", inventory.status_counts)
    table("Status categories", inventory.status_category_counts)
    table("Age since last update", inventory.age_bucket_counts)

    lines.extend(
        [
            "## Grooming waves",
            "",
            "| Wave | Issues | Parent groups | Type mix |",
            "|---:|---:|---:|---|",
        ]
    )
    for wave in inventory.waves:
        type_mix = ", ".join(
            f"{issue_type}: {count}" for issue_type, count in wave.issue_type_counts.items()
        )
        lines.append(
            f"| {wave.wave_number} | {len(wave.keys)} | {len(wave.parent_groups)} | {type_mix} |"
        )

    if inventory.orphan_subtask_keys:
        lines.extend(["", "## Orphaned subtasks", ""])
        lines.extend(f"- {key}" for key in inventory.orphan_subtask_keys)
    if inventory.oversized_parent_groups:
        lines.extend(
            [
                "",
                "## Parent groups larger than one wave",
                "",
                "These groups were split across waves and require extra cross-wave review.",
                "",
            ]
        )
        lines.extend(
            f"- {key}: {count}" for key, count in inventory.oversized_parent_groups.items()
        )
    lines.append("")
    return "\n".join(lines)
