from conftest import make_issue

from jira_groomer.inventory import build_inventory, inventory_to_markdown


def test_inventory_keeps_parent_group_together() -> None:
    parent = make_issue("WEB-1", summary="Checkout outcome")
    parent.issue_type = "Epic"
    child_one = make_issue("WEB-2", summary="Pay by card")
    child_one.parent_key = "WEB-1"
    child_two = make_issue("WEB-3", summary="See payment failure")
    child_two.parent_key = "WEB-1"
    unrelated = make_issue("WEB-4", summary="Export orders")

    inventory = build_inventory(
        [parent, unrelated, child_one, child_two],
        source_jql="project = WEB",
        wave_size=3,
    )
    assert inventory.issue_count == 4
    assert inventory.waves[0].keys == ["WEB-1", "WEB-2", "WEB-3"]
    assert inventory.waves[1].keys == ["WEB-4"]
    assert "Proposed grooming waves: 2" in inventory_to_markdown(inventory)


def test_inventory_flags_orphaned_subtask_and_oversized_group() -> None:
    parent = make_issue("WEB-1")
    children = []
    for number in range(2, 6):
        child = make_issue(f"WEB-{number}")
        child.parent_key = "WEB-1"
        child.is_subtask = True
        children.append(child)
    orphan = make_issue("WEB-9")
    orphan.parent_key = "WEB-404"
    orphan.is_subtask = True

    inventory = build_inventory(
        [parent, *children, orphan],
        source_jql="project = WEB",
        wave_size=3,
    )
    assert inventory.oversized_parent_groups == {"WEB-1": 5}
    assert inventory.orphan_subtask_keys == ["WEB-9"]
    assert all(len(wave.keys) <= 3 for wave in inventory.waves)
