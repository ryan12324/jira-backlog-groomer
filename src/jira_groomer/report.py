from __future__ import annotations

from collections import Counter

from .models import GroomingPlan


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def plan_to_markdown(plan: GroomingPlan) -> str:
    counts = Counter(action.kind for action in plan.actions)
    lines = [
        f"# Jira grooming plan {plan.run_id}",
        "",
        f"- Created: {plan.created_at.isoformat()}",
        f"- Source JQL: `{plan.source_jql}`",
        f"- Source issues: {plan.source_issue_count}",
        f"- Source fingerprint: `{plan.source_fingerprint}`",
        "- Status: proposal only; no Jira writes occurred while generating this report.",
        "",
        "## Proposed changes",
        "",
        "| Operation | Count |",
        "|---|---:|",
    ]
    labels = {
        "update_issue": "Rewrite existing stories",
        "create_issue": "Create vertical split stories",
        "link_issue": "Create issue relationships",
        "rank_issues": "Apply ranked backlog order",
        "archive_issue": "Archive issues",
    }
    for kind, label in labels.items():
        lines.append(f"| {label} | {counts[kind]} |")

    if plan.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in plan.warnings)

    lines.extend(
        [
            "",
            "## Proposed backlog order",
            "",
            "| Rank | Issue/ref | Score | Basis |",
            "|---:|---|---:|---|",
        ]
    )
    for index, item in enumerate(plan.ranked_backlog, start=1):
        lines.append(
            f"| {index} | {_cell(item.key_or_ref)} | {item.score:.4f} | {_cell(item.explanation)} |"
        )

    lines.extend(["", "## Story assessments", ""])
    for assessment in plan.assessments:
        story = assessment.story
        lines.extend(
            [
                f"### {assessment.source_key}: {story.title}",
                "",
                f"- Recommendation: `{assessment.recommendation}`",
                f"- Confidence: {assessment.confidence:.0%}",
                f"- Current INVEST quality: {assessment.invest.overall_quality}/100",
                f"- Rationale: {assessment.rationale}",
                "",
                (
                    f"**User story:** As a {story.persona}, I want {story.need}, "
                    f"so that {story.benefit}."
                ),
                "",
                "**Acceptance criteria**",
                "",
            ]
        )
        for criterion in story.acceptance_criteria:
            lines.append(
                f"- Given {criterion.given}, when {criterion.when}, then {criterion.then}."
            )
        if story.open_questions:
            lines.extend(["", "**Open questions**", ""])
            lines.extend(f"- {question}" for question in story.open_questions)
        if assessment.quality_findings:
            lines.extend(["", "**Quality findings**", ""])
            lines.extend(f"- {finding}" for finding in assessment.quality_findings)
        if assessment.split_stories:
            lines.extend(["", "**Proposed vertical splits**", ""])
            lines.extend(f"- {split.title}" for split in assessment.split_stories)
        if assessment.link_suggestions:
            lines.extend(["", "**Proposed relationships**", ""])
            lines.extend(
                f"- {suggestion.relationship} {suggestion.target_key} "
                f"({suggestion.confidence:.0%}): {suggestion.rationale}"
                for suggestion in assessment.link_suggestions
            )
        if assessment.archive_evidence:
            lines.extend(["", "**Archive evidence**", ""])
            lines.extend(f"- {evidence}" for evidence in assessment.archive_evidence)
        lines.append("")

    lines.extend(
        [
            "## Action manifest",
            "",
            "| Order | Action ID | Type | Target | Rationale |",
            "|---:|---|---|---|---|",
        ]
    )
    for index, action in enumerate(plan.actions, start=1):
        target = (
            getattr(action, "key", None)
            or getattr(action, "temp_ref", None)
            or getattr(action, "outward_ref", None)
            or ", ".join(getattr(action, "issue_refs", []))
        )
        lines.append(
            f"| {index} | `{action.action_id}` | `{action.kind}` | {_cell(target)} | "
            f"{_cell(action.rationale)} |"
        )

    lines.extend(
        [
            "",
            "## Approval notes",
            "",
            "Review the JSON plan as the source of truth. Enabling write gates in the TOML "
            "does not change this plan's content. Application checks that issues have not changed "
            "since planning, records every result, and stops on the first failed action.",
            "",
        ]
    )
    return "\n".join(lines)
