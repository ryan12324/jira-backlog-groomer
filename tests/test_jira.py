import json

import httpx

from jira_groomer.config import JiraCredentials
from jira_groomer.jira import JiraClient


def _raw_issue(key: str) -> dict:
    return {
        "id": key.split("-")[-1],
        "key": key,
        "fields": {
            "summary": f"Summary {key}",
            "description": None,
            "issuetype": {"name": "Story", "subtask": False},
            "status": {
                "name": "To Do",
                "statusCategory": {"name": "To Do"},
            },
            "labels": [],
            "components": [],
            "subtasks": [],
            "issuelinks": [],
            "created": "2026-01-01T10:00:00.000+0000",
            "updated": "2026-01-02T10:00:00.000+0000",
        },
    }


def test_enhanced_search_paginates_with_next_page_token() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if "nextPageToken" not in body:
            return httpx.Response(
                200,
                json={"issues": [_raw_issue("WEB-1")], "nextPageToken": "next"},
            )
        return httpx.Response(
            200,
            json={"issues": [_raw_issue("WEB-2")], "isLast": True},
        )

    client = JiraClient(
        "https://example.atlassian.net",
        JiraCredentials(
            auth_mode="basic",
            email="bot@example.com",
            api_token="token",
        ),
        transport=httpx.MockTransport(handler),
    )
    try:
        issues = client.search_issues("project = WEB", ["summary"])
    finally:
        client.close()
    assert [issue.key for issue in issues] == ["WEB-1", "WEB-2"]
    assert requests[1]["nextPageToken"] == "next"
