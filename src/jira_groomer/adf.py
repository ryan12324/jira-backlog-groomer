from __future__ import annotations

from collections.abc import Iterable

from .models import AcceptanceCriterion, StorySpec


def adf_to_text(value: object | None) -> str:
    """Extract readable text from Jira's Atlassian Document Format or plain text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(filter(None, (adf_to_text(item) for item in value))).strip()
    if not isinstance(value, dict):
        return str(value)

    node_type = value.get("type")
    if node_type == "text":
        return str(value.get("text", ""))
    if node_type == "hardBreak":
        return "\n"

    children = value.get("content", [])
    parts = [adf_to_text(child) for child in children]
    separator = "\n" if node_type in {"doc", "bulletList", "orderedList", "listItem"} else ""
    return separator.join(part for part in parts if part).strip()


def _text(value: str) -> dict:
    return {"type": "text", "text": value}


def _paragraph(value: str) -> dict:
    return {"type": "paragraph", "content": [_text(value)]}


def _heading(value: str, level: int = 2) -> dict:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [_text(value)],
    }


def _bullet_list(items: Iterable[str]) -> dict:
    return {
        "type": "bulletList",
        "content": [
            {"type": "listItem", "content": [_paragraph(item)]} for item in items if item.strip()
        ],
    }


def _acceptance_criterion(criterion: AcceptanceCriterion) -> str:
    return f"Given {criterion.given}, when {criterion.when}, then {criterion.then}."


def story_to_adf(
    story: StorySpec,
    *,
    original_description: str = "",
    preserve_original: bool = True,
) -> dict:
    """Render a cross-functional user story as Jira Cloud ADF."""
    content: list[dict] = [
        _heading("User story"),
        _paragraph(f"As a {story.persona}, I want {story.need}, so that {story.benefit}."),
        _heading("Context"),
        _paragraph(story.context),
        _heading("Acceptance criteria"),
        _bullet_list(_acceptance_criterion(item) for item in story.acceptance_criteria),
    ]

    sections: list[tuple[str, list[str]]] = [
        ("Non-functional requirements", story.non_functional_requirements),
        ("Dependencies", story.dependencies),
        ("Out of scope", story.out_of_scope),
        ("Open questions", story.open_questions),
        ("Frontend considerations", story.cross_functional_notes.frontend),
        ("Backend considerations", story.cross_functional_notes.backend),
        ("Shared delivery considerations", story.cross_functional_notes.shared),
    ]
    for heading, items in sections:
        if items:
            content.extend([_heading(heading), _bullet_list(items)])

    if preserve_original and original_description.strip():
        content.extend(
            [
                _heading("Original ticket notes"),
                _paragraph(original_description.strip()),
            ]
        )

    content.extend(
        [
            _heading("Grooming provenance"),
            _paragraph(
                "AI-assisted draft. A product owner and delivery team must confirm scope, "
                "acceptance criteria, dependencies, and estimates before commitment."
            ),
        ]
    )
    return {"version": 1, "type": "doc", "content": content}
