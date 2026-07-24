from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

import httpx

from .adf import adf_to_text
from .config import JiraCredentials
from .errors import JiraError
from .models import JiraIssue


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


class JiraClient:
    """Small Jira Cloud REST v3 client with bounded retries and no secret logging."""

    def __init__(
        self,
        base_url: str,
        credentials: JiraCredentials,
        *,
        timeout_seconds: float = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "jira-backlog-groomer/0.1",
        }
        auth: httpx.Auth | None = None
        if credentials.auth_mode == "basic":
            assert credentials.email and credentials.api_token
            auth = httpx.BasicAuth(
                credentials.email,
                credentials.api_token.get_secret_value(),
            )
        else:
            assert credentials.bearer_token
            headers["Authorization"] = f"Bearer {credentials.bearer_token.get_secret_value()}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            auth=auth,
            timeout=timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JiraClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        attempts: int = 4,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._client.request(method, path, params=params, json=json)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                time.sleep(min(2**attempt, 8))
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt == attempts - 1:
                    return response
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else min(2**attempt, 8)
                except ValueError:
                    delay = min(2**attempt, 8)
                time.sleep(min(delay, 30))
                continue
            return response

        raise JiraError(f"Jira {method} {path} failed: {last_error}")

    def _json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        response = self._request(method, path, params=params, json=json)
        if response.status_code not in expected:
            body = response.text[:1000].replace("\n", " ")
            raise JiraError(f"Jira {method} {path} returned {response.status_code}: {body}")
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise JiraError(f"Jira {method} {path} returned invalid JSON") from exc

    def myself(self) -> dict[str, Any]:
        return self._json("GET", "/rest/api/3/myself")

    def server_info(self) -> dict[str, Any]:
        return self._json("GET", "/rest/api/3/serverInfo")

    def search_issues(
        self,
        jql: str,
        fields: list[str],
        *,
        max_issues: int | None = None,
    ) -> list[JiraIssue]:
        issues: list[JiraIssue] = []
        next_page_token: str | None = None
        while True:
            remaining = None if max_issues is None else max_issues - len(issues)
            if remaining is not None and remaining <= 0:
                break
            page_size = min(100, remaining) if remaining is not None else 100
            body: dict[str, Any] = {
                "jql": jql,
                "fields": fields,
                "maxResults": page_size,
            }
            if next_page_token:
                body["nextPageToken"] = next_page_token
            page = self._json("POST", "/rest/api/3/search/jql", json=body)
            page_issues = page.get("issues", [])
            issues.extend(self._normalize_issue(item) for item in page_issues)
            next_page_token = page.get("nextPageToken")
            if page.get("isLast") is True or not next_page_token or not page_issues:
                break
        return issues

    def get_issue(self, key: str, fields: list[str] | None = None) -> JiraIssue:
        selected = fields or [
            "summary",
            "description",
            "issuetype",
            "status",
            "priority",
            "labels",
            "components",
            "parent",
            "subtasks",
            "issuelinks",
            "created",
            "updated",
        ]
        raw = self._json(
            "GET",
            f"/rest/api/3/issue/{key}",
            params={"fields": ",".join(selected)},
        )
        return self._normalize_issue(raw)

    def find_issue_by_label(self, project_key: str, label: str) -> JiraIssue | None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", project_key):
            raise JiraError("Unsafe project key supplied to label lookup")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", label):
            raise JiraError("Unsafe label supplied to label lookup")
        matches = self.search_issues(
            f'project = "{project_key}" AND labels = "{label}"',
            [
                "summary",
                "description",
                "issuetype",
                "status",
                "priority",
                "labels",
                "components",
                "parent",
                "subtasks",
                "issuelinks",
                "created",
                "updated",
            ],
            max_issues=2,
        )
        if len(matches) > 1:
            raise JiraError(f"Multiple issues have idempotency label {label}")
        return matches[0] if matches else None

    def update_issue(
        self,
        key: str,
        *,
        summary: str,
        description: dict,
        labels: list[str],
    ) -> None:
        self._json(
            "PUT",
            f"/rest/api/3/issue/{key}",
            json={
                "fields": {
                    "summary": summary,
                    "description": description,
                    "labels": labels,
                }
            },
            expected=(204,),
        )

    def create_issue(
        self,
        *,
        project_key: str,
        issue_type: str,
        summary: str,
        description: dict,
        labels: list[str],
        extra_fields: dict[str, Any] | None = None,
    ) -> str:
        fields: dict[str, Any] = dict(extra_fields or {})
        fields.update(
            {
                "project": {"key": project_key},
                "issuetype": {"name": issue_type},
                "summary": summary,
                "description": description,
                "labels": labels,
            }
        )
        result = self._json(
            "POST",
            "/rest/api/3/issue",
            json={"fields": fields},
            expected=(201,),
        )
        return str(result["key"])

    def link_issues(
        self,
        *,
        outward_key: str,
        inward_key: str,
        link_type: str,
    ) -> None:
        self._json(
            "POST",
            "/rest/api/3/issueLink",
            json={
                "type": {"name": link_type},
                "outwardIssue": {"key": outward_key},
                "inwardIssue": {"key": inward_key},
            },
            expected=(201,),
        )

    def issue_has_link(self, source_key: str, target_key: str, link_type: str) -> bool:
        issue = self.get_issue(source_key, fields=["issuelinks", "updated", "created", "summary"])
        for link in issue.raw_fields.get("issuelinks", []):
            linked = link.get("outwardIssue") or link.get("inwardIssue") or {}
            if linked.get("key") == target_key and link.get("type", {}).get("name") == link_type:
                return True
        return False

    def archive_issues(self, issue_keys: list[str]) -> dict[str, Any]:
        if not issue_keys:
            return {"numberOfIssuesUpdated": 0}
        return self._json(
            "PUT",
            "/rest/api/3/issue/archive",
            json={"issueIdsOrKeys": issue_keys},
            expected=(200,),
        )

    def unarchive_issues(self, issue_keys: list[str]) -> dict[str, Any]:
        if not issue_keys:
            return {"numberOfIssuesUpdated": 0}
        return self._json(
            "PUT",
            "/rest/api/3/issue/unarchive",
            json={"issueIdsOrKeys": issue_keys},
            expected=(200,),
        )

    def rank_issues(self, issue_keys: list[str], rank_before_issue: str) -> None:
        """Rank in stable chunks; Jira Software allows at most 50 issues per request."""
        previous: str | None = None
        for start in range(0, len(issue_keys), 50):
            chunk = issue_keys[start : start + 50]
            body: dict[str, Any] = {"issues": chunk}
            if previous:
                body["rankAfterIssue"] = previous
            else:
                body["rankBeforeIssue"] = rank_before_issue
            self._json(
                "PUT",
                "/rest/agile/1.0/issue/rank",
                json=body,
                expected=(204,),
            )
            previous = chunk[-1]

    @staticmethod
    def _normalize_issue(raw: dict[str, Any]) -> JiraIssue:
        fields = raw.get("fields", {})
        status = fields.get("status") or {}
        status_category = status.get("statusCategory") or {}
        priority = fields.get("priority") or {}
        issue_type = fields.get("issuetype") or {}
        parent = fields.get("parent") or {}
        created = fields.get("created") or fields.get("updated")
        updated = fields.get("updated") or created
        if not created or not updated:
            raise JiraError(f"Issue {raw.get('key', '<unknown>')} is missing timestamps")

        linked_keys: list[str] = []
        for link in fields.get("issuelinks") or []:
            linked = link.get("outwardIssue") or link.get("inwardIssue") or {}
            if linked.get("key"):
                linked_keys.append(
                    str(link["outwardIssue"]["key"])
                    if link.get("outwardIssue")
                    else str(link["inwardIssue"]["key"])
                )

        return JiraIssue(
            id=str(raw.get("id", "")),
            key=str(raw["key"]),
            summary=str(fields.get("summary") or ""),
            description_text=adf_to_text(fields.get("description")),
            issue_type=str(issue_type.get("name") or ""),
            is_subtask=bool(issue_type.get("subtask", False)),
            status_name=str(status.get("name") or ""),
            status_category=str(status_category.get("name") or ""),
            priority=str(priority.get("name")) if priority.get("name") else None,
            labels=[str(item) for item in (fields.get("labels") or [])],
            components=[
                str(item.get("name"))
                for item in (fields.get("components") or [])
                if item.get("name")
            ],
            parent_key=str(parent.get("key")) if parent.get("key") else None,
            subtask_keys=[
                str(item["key"]) for item in (fields.get("subtasks") or []) if item.get("key")
            ],
            linked_keys=linked_keys,
            created=_parse_datetime(str(created)),
            updated=_parse_datetime(str(updated)),
            raw_fields=fields,
        )
