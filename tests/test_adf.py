from conftest import make_story

from jira_groomer.adf import adf_to_text, story_to_adf


def test_story_adf_contains_vertical_story_and_original_notes() -> None:
    document = story_to_adf(
        make_story(),
        original_description="legacy implementation note",
        preserve_original=True,
    )
    text = adf_to_text(document)
    assert "As a customer" in text
    assert "Given a customer has a valid basket" in text
    assert "Frontend considerations" in text
    assert "Backend considerations" in text
    assert "legacy implementation note" in text


def test_adf_to_text_accepts_plain_text() -> None:
    assert adf_to_text(" plain description ") == "plain description"


def test_bug_adf_uses_defect_structure() -> None:
    story = make_story("Checkout shows duplicate confirmation")
    story.delivery_kind = "bug_fix"
    story.observed_behavior = "Two confirmation messages are shown."
    story.expected_behavior = "Exactly one confirmation message is shown."
    story.reproduction_steps = ["Submit a valid order", "Open the confirmation page"]
    text = adf_to_text(story_to_adf(story, preserve_original=False))
    assert "Bug outcome" in text
    assert "Observed behavior" in text
    assert "Two confirmation messages" in text
    assert "Reproduction steps" in text
