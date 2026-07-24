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
