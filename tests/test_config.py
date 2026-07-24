from jira_groomer.config import planning_config_sha256


def test_planning_digest_ignores_only_write_gates(settings) -> None:
    original = planning_config_sha256(settings)
    settings.write_policy.allow_issue_updates = True
    settings.write_policy.allow_issue_creation = True
    settings.archive.allow_apply = True
    assert planning_config_sha256(settings) == original

    settings.quality.minimum_rewrite_score = 90
    assert planning_config_sha256(settings) != original
