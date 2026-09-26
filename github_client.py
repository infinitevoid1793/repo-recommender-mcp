"""Thin wrapper around the GitHub REST API used by the MCP tools."""

import base64
from urllib.parse import quote

import requests

API_BASE = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str):
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._session.get(f"{API_BASE}{path}", params=params)
        if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
            raise GitHubError(
                "GitHub rate limit hit. Search endpoints are limited to ~30 requests/minute "
                "even with a token — wait a bit before retrying."
            )
        if not resp.ok:
            raise GitHubError(f"GitHub API error {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def search_issues(self, query: str, max_results: int) -> list[dict]:
        data = self._get(
            "/search/issues",
            params={"q": query, "per_page": min(max_results, 100)},
        )
        return data.get("items", [])[:max_results]

    def search_repositories(self, query: str, per_page: int = 30, page: int = 1) -> list[dict]:
        data = self._get(
            "/search/repositories", params={"q": query, "per_page": per_page, "page": page}
        )
        return data.get("items", [])

    def get_pulls(self, repo: str, page: int = 1, per_page: int = 30) -> list[dict]:
        return self._get(
            f"/repos/{repo}/pulls",
            params={
                "state": "all",
                "sort": "created",
                "direction": "desc",
                "per_page": per_page,
                "page": page,
            },
        )

    def get_repo(self, repo: str) -> dict:
        return self._get(f"/repos/{repo}")

    def get_readme_excerpt(self, repo: str, max_chars: int = 1000) -> str:
        try:
            data = self._get(f"/repos/{repo}/readme")
        except GitHubError:
            return ""
        content = data.get("content", "")
        try:
            decoded = base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:
            return ""
        return decoded[:max_chars]

    def get_issue_comments(
        self, repo: str, issue_number: int, page: int = 1, per_page: int = 30
    ) -> list[dict]:
        return self._get(
            f"/repos/{repo}/issues/{issue_number}/comments",
            params={"page": page, "per_page": per_page},
        )

    def get_issue(self, repo: str, issue_number: int) -> dict:
        return self._get(f"/repos/{repo}/issues/{issue_number}")

    def get_contents(self, repo: str, path: str, ref: str | None = None) -> dict | list:
        params = {"ref": ref} if ref else None
        return self._get(f"/repos/{repo}/contents/{quote(path.strip('/'), safe='/')}", params)

    def get_repo_issue_counts(self, repo: str) -> dict:
        open_data = self._get(
            "/search/issues", params={"q": f"repo:{repo} is:issue is:open", "per_page": 1}
        )
        closed_data = self._get(
            "/search/issues", params={"q": f"repo:{repo} is:issue is:closed", "per_page": 1}
        )
        return {
            "open_count": open_data.get("total_count", 0),
            "closed_count": closed_data.get("total_count", 0),
        }
