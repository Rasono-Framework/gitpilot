"""Thin, typed wrapper around the GitHub REST API.

Every method goes through `_request`, which:
- Attaches a fresh installation token (refreshing on 401).
- Surfaces friendly errors with the request id so users can quote it in
  a support ticket.
- Returns parsed JSON.

We deliberately do not pull in `PyGithub` to keep the dependency surface
small and predictable. Each call site uses only the fields it needs.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

from .auth import API_VERSION, GitHubAppAuth, USER_AGENT

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

# Endpoints we hit are idempotent on 5xx after a small backoff.
DEFAULT_RETRY = 3
RETRY_BACKOFF = 1.5  # multiplied by attempt index


class GitHubApiError(RuntimeError):
    """Raised when GitHub returns a non-2xx response that we cannot recover from."""

    def __init__(self, status: int, message: str, request_id: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.request_id = request_id

    def __str__(self) -> str:
        if self.request_id:
            return f"[{self.status}] {self.message} (request_id={self.request_id})"
        return f"[{self.status}] {self.message}"


@dataclass
class RateLimit:
    remaining: int
    reset_at: int  # epoch seconds
    limit: int


class GitHubClient:
    def __init__(
        self,
        auth: GitHubAppAuth,
        *,
        user_token: Optional[str] = None,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        """Build a GitHub API client.

        Args:
            auth: App auth (used to mint installation tokens when ``user_token``
                is ``None``). Required even in OAuth mode so ``whoami`` and
                ``list-installations`` can fall back to the App JWT.
            user_token: Optional OAuth user access token (``gho_...``). When
                set, takes precedence over installation tokens for every API
                call. This is what we use after ``auth login``.
            base_url: Override the API base URL. Defaults to
                ``https://api.github.com``. Used by tests to point the client
                at a local mock server; also useful for GitHub Enterprise
                Server (e.g. ``https://github.acme.com/api/v3``).
            session: Optional pre-configured ``requests.Session`` (tests).
        """
        self.auth = auth
        self.user_token = user_token
        self.base_url = (base_url or GITHUB_API).rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.setdefault("Accept", "application/vnd.github+json")
        self.session.headers.setdefault("X-GitHub-Api-Version", API_VERSION)
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self._last_rate_limit: Optional[RateLimit] = None

    # ------------------------------------------------------------------ core
    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        attempts = 0
        last_exc: Optional[requests.RequestException] = None

        while attempts < DEFAULT_RETRY:
            attempts += 1
            # User token takes precedence: it's a gho_... that authenticates
            # as the user, and we don't need to refresh it. The App auth is
            # only used when no user token is available.
            token = self.user_token or self.auth.get_token()
            headers = dict(kwargs.pop("headers", {}) or {})
            headers["Authorization"] = f"Bearer {token}"

            try:
                resp = self.session.request(
                    method, url, headers=headers, timeout=30, **kwargs
                )
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(RETRY_BACKOFF * attempts)
                continue

            self._capture_rate_limit(resp)
            # Only the App auth has a refresh path; user tokens are static.
            if (
                resp.status_code == 401
                and self.user_token is None
                and attempts < DEFAULT_RETRY
            ):
                self.auth.invalidate()
                continue

            if 500 <= resp.status_code < 600 and attempts < DEFAULT_RETRY:
                logger.warning(
                    "GitHub %s on %s %s — retrying (%d/%d)",
                    resp.status_code, method, path, attempts, DEFAULT_RETRY,
                )
                time.sleep(RETRY_BACKOFF * attempts)
                continue

            if not resp.ok:
                request_id = resp.headers.get("x-github-request-id", "")
                try:
                    body = resp.json()
                    msg = body.get("message") or resp.text
                except ValueError:
                    msg = resp.text
                raise GitHubApiError(resp.status_code, msg, request_id)

            return resp

        raise GitHubApiError(0, f"Network error after {DEFAULT_RETRY} attempts: {last_exc}")

    def _capture_rate_limit(self, resp: requests.Response) -> None:
        try:
            remaining = int(resp.headers.get("x-ratelimit-remaining", "-1"))
            limit = int(resp.headers.get("x-ratelimit-limit", "-1"))
            reset = int(resp.headers.get("x-ratelimit-reset", "0"))
        except (TypeError, ValueError):
            return
        if remaining >= 0 and reset:
            self._last_rate_limit = RateLimit(remaining=remaining, reset_at=reset, limit=limit)

    @property
    def last_rate_limit(self) -> Optional[RateLimit]:
        return self._last_rate_limit

    # ------------------------------------------------------------------ repos
    def create_repo(
        self,
        org: str,
        name: str,
        *,
        private: bool = True,
        description: str = "",
        auto_init: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "private": private,
            "description": description,
            "auto_init": auto_init,
        }
        resp = self._request("POST", f"/orgs/{org}/repos", json=payload)
        return resp.json()

    def get_repo(self, owner: str, name: str) -> dict[str, Any]:
        resp = self._request("GET", f"/repos/{owner}/{name}")
        return resp.json()

    def list_repos(self, org: str, per_page: int = 30) -> list[dict[str, Any]]:
        resp = self._request("GET", f"/orgs/{org}/repos", params={"per_page": per_page})
        return resp.json()

    def delete_repo(self, owner: str, name: str) -> None:
        self._request("DELETE", f"/repos/{owner}/{name}")

    # ----------------------------------------------------------------- branches
    def get_branch(self, owner: str, repo: str, branch: str) -> dict[str, Any]:
        resp = self._request("GET", f"/repos/{owner}/{repo}/branches/{branch}")
        return resp.json()

    def get_default_branch_sha(self, owner: str, repo: str) -> str:
        repo_data = self.get_repo(owner, repo)
        default = repo_data.get("default_branch")
        if not default:
            raise GitHubApiError(0, f"Repository {owner}/{repo} has no default_branch")
        return self.get_branch(owner, repo, default)["commit"]["sha"]

    def create_branch(self, owner: str, repo: str, branch: str, *, from_branch: Optional[str] = None) -> dict[str, Any]:
        if from_branch:
            sha = self.get_branch(owner, repo, from_branch)["commit"]["sha"]
        else:
            sha = self.get_default_branch_sha(owner, repo)

        resp = self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        return resp.json()

    # ------------------------------------------------------------------- files
    def get_file(self, owner: str, repo: str, path: str, *, ref: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Return the contents of a file, or None if it doesn't exist.

        ``_request`` raises on any non-2xx response, so we catch the 404 here
        specifically and translate it to ``None`` — the caller (notably
        ``push_file``) treats "not found" as the normal "create a new file"
        path, distinct from other 4xx errors.
        """
        params = {"ref": ref} if ref else None
        try:
            resp = self._request("GET", f"/repos/{owner}/{repo}/contents/{path}", params=params)
        except GitHubApiError as exc:
            if exc.status == 404:
                return None
            raise
        return resp.json()

    def push_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        *,
        message: str,
        branch: str,
        update: bool = True,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if update:
            existing = self.get_file(owner, repo, path, ref=branch)
            if existing and "sha" in existing:
                body["sha"] = existing["sha"]
        resp = self._request("PUT", f"/repos/{owner}/{repo}/contents/{path}", json=body)
        return resp.json()

    def push_files(
        self,
        owner: str,
        repo: str,
        files: list[dict[str, str]],
        *,
        message: str,
        branch: str,
    ) -> dict[str, Any]:
        """Atomic multi-file commit using the Git Data API (trees + commits + refs).

        Each file in `files` must be a dict with keys: ``path``, ``content``.
        """
        if not files:
            raise ValueError("files must not be empty")

        # 1. Resolve the branch head commit.
        head = self.get_branch(owner, repo, branch)
        head_sha = head["commit"]["sha"]

        # 2. Get the commit's tree.
        commit = self._request(
            "GET", f"/repos/{owner}/{repo}/git/commits/{head_sha}"
        ).json()
        base_tree_sha = commit["tree"]["sha"]

        # 3. Create blobs for each file.
        tree_items: list[dict[str, Any]] = []
        for f in files:
            blob = self._request(
                "POST",
                f"/repos/{owner}/{repo}/git/blobs",
                json={
                    "content": f["content"],
                    "encoding": "utf-8",
                },
            ).json()
            tree_items.append(
                {"path": f["path"], "mode": "100644", "type": "blob", "sha": blob["sha"]}
            )

        # 4. Create a new tree.
        new_tree = self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/trees",
            json={"base_tree": base_tree_sha, "tree": tree_items},
        ).json()

        # 5. Create a commit on the branch.
        new_commit = self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/commits",
            json={"message": message, "tree": new_tree["sha"], "parents": [head_sha]},
        ).json()

        # 6. Update the branch ref to point at the new commit.
        self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
            json={"sha": new_commit["sha"]},
        )
        return new_commit
