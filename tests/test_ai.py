from conftest import make_assessment, make_issue

from jira_groomer.ai import OpenAIBacklogAnalyst
from jira_groomer.models import AssessmentBatch


class FailingResponses:
    def parse(self, **_):
        raise AssertionError("OpenAI should not be called when a valid cache entry exists")


class FailingClient:
    responses = FailingResponses()


def test_valid_batch_cache_avoids_openai_request(settings, tmp_path) -> None:
    analyst = object.__new__(OpenAIBacklogAnalyst)
    analyst.config = settings.ai
    analyst.product = settings.product
    analyst.client = FailingClient()
    issue = make_issue()
    payload = analyst._batch_payload([issue], {issue.key: []})
    cache_path = analyst._cache_path(payload, tmp_path)
    assert cache_path is not None
    cache_path.write_text(
        AssessmentBatch(assessments=[make_assessment()]).model_dump_json(),
        encoding="utf-8",
    )
    result = analyst._analyze_batch([issue], {issue.key: []}, tmp_path)
    assert result[0].source_key == issue.key
