"""Async GitHub App auth + REST client for the FastAPI service."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import jwt

from .client import GitHubApiError
from .config import Config

logger = logging.getLogger(__name__)

API_VERSION = "2022-11-28"
USER_AGENT = "github-api-service/0.1"
JWT_TTL_SECONDS = 9 * 60
TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60
DEFAULT_RETRY = 3
RETRY_BACKOFF = 0.2


class AsyncGitHubAuthError(RuntimeError):
    """Raised when GitHub App auth fails."""


@dataclass
class _CachedToken:
    token: str
    expires_at: float


class AsyncGitHubAppAuth:
    """JWT minting + installation token cache for concurrent async requests."""

    def __init__(self, config: Config, client: httpx.AsyncClient) -> None:
        self._app_id = config.app_id
        self._private_key = config.private_key
        self._installation_id = config.installation_id
        self._base_url = config.github_api_base_url.rstrip("/")
        self._http = client
        self._cached: Optional[_CachedToken] = None
        self._lock = asyncio.Lock()

    def _mint_app_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iat": now - 30,
            "exp": now + JWT_TTL_SECONDS,
            "iss": self._app_id,
        }
        try:
            return jwt.encode(payload, self._private_key, algorithm="RS256")
        except jwt.InvalidKeyError as exc:
            raise AsyncGitHubAuthError(
                "GITHUB_PRIVATE_KEY is not a valid RSA PEM key."
            ) from exc

    async def get_token(self, force_refresh: bool = False) -> str:
        async with self._lock:
            now = time.time()
            if (
                not force_refresh
                and self._cached
                and now < self._cached.expires_at - TOKEN_REFRESH_MARGIN_SECONDS
            ):
                return self._cached.token

            app_jwt = self._mint_app_jwt()
            headers = {
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": USER_AGENT,
            }
            try:
                resp = await self._http.post(
                    f"{self._base_url}/app/installations/{self._installation_id}/access_tokens",
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                raise AsyncGitHubAuthError(
                    f"Network error while fetching installation token: {exc}"
                ) from exc

            if resp.status_code == 401:
                raise AsyncGitHubAuthError("GitHub rejected the App JWT (401).")
            if resp.status_code == 404:
                hint = await self._list_installations_hint(app_jwt)
                raise AsyncGitHubAuthError(
                    f"Installation {self._installation_id} not found (404).\n{hint}"
                )
            if not resp.is_success:
                raise AsyncGitHubAuthError(
                    f"Failed to obtain installation token: {resp.status_code} {resp.text[:300]}"
                )

            data = resp.json()
            token = data.get("token")
            expires_at_iso = data.get("expires_at")
            if not token or not expires_at_iso:
                raise AsyncGitHubAuthError(
                    f"Unexpected installation-token response: {data!r}"
                )

            try:
                dt = datetime.fromisoformat(expires_at_iso.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                expires_epoch = dt.timestamp()
            except (TypeError, ValueError) as exc:
                raise AsyncGitHubAuthError(
                    f"Could not parse installation token expiry: {expires_at_iso!r}"
                ) from exc

            self._cached = _CachedToken(token=token, expires_at=expires_epoch)
            logger.info("Obtained fresh installation token (expires %s)", expires_at_iso)
            return token

    def invalidate(self) -> None:
        self._cached = None

    async def _list_installations_hint(self, app_jwt: str) -> str:
        try:
            resp = await self._http.get(
                f"{self._base_url}/app/installations",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": API_VERSION,
                    "User-Agent": USER_AGENT,
                },
            )
            if not resp.is_success:
                return "(hint unavailable: could not list installations)"
            installs = resp.json()
            if not installs:
                return "This GitHub App has no installations yet."
            return "Available installations: " + ", ".join(
                f"{inst.get('id')}:{(inst.get('account') or {}).get('login')}"
                for inst in installs
            )
        except httpx.HTTPError as exc:
            return f"(hint unavailable: {exc})"


@dataclass
class RateLimit:
    remaining: int
    reset_at: int
    limit: int


class AsyncGitHubClient:
    """Async GitHub REST client with connection reuse and retry logic."""

    def __init__(
        self,
        auth: AsyncGitHubAppAuth,
        client: httpx.AsyncClient,
        *,
        user_token: str = "",
        base_url: str = "https://api.github.com",
    ) -> None:
        self.auth = auth
        self.http = client
        self.user_token = user_token
        self.base_url = base_url.rstrip("/")
        self._last_rate_limit: Optional[RateLimit] = None
        self._owner_type_cache: dict[str, str] = {}
        self._viewer_login: Optional[str] = None

    @property
    def last_rate_limit(self) -> Optional[RateLimit]:
        return self._last_rate_limit

    def _capture_rate_limit(self, resp: httpx.Response) -> None:
        try:
            remaining = int(resp.headers.get("x-ratelimit-remaining", "-1"))
            limit = int(resp.headers.get("x-ratelimit-limit", "-1"))
            reset = int(resp.headers.get("x-ratelimit-reset", "0"))
        except (TypeError, ValueError):
            return
        if remaining >= 0 and reset:
            self._last_rate_limit = RateLimit(remaining=remaining, reset_at=reset, limit=limit)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None

        for attempt in range(1, DEFAULT_RETRY + 1):
            token = self.user_token or await self.auth.get_token()
            request_kwargs = dict(kwargs)
            headers = dict(request_kwargs.pop("headers", {}) or {})
            headers["Authorization"] = f"Bearer {token}"
            headers["Accept"] = "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = API_VERSION
            headers["User-Agent"] = USER_AGENT

            try:
                resp = await self.http.request(method, url, headers=headers, **request_kwargs)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt == DEFAULT_RETRY:
                    break
                await asyncio.sleep(RETRY_BACKOFF * attempt)
                continue

            self._capture_rate_limit(resp)
            if resp.status_code == 401 and not self.user_token and attempt < DEFAULT_RETRY:
                self.auth.invalidate()
                await asyncio.sleep(RETRY_BACKOFF * attempt)
                continue
            if 500 <= resp.status_code < 600 and attempt < DEFAULT_RETRY:
                await asyncio.sleep(RETRY_BACKOFF * attempt)
                continue
            if not resp.is_success:
                request_id = resp.headers.get("x-github-request-id", "")
                try:
                    message = resp.json().get("message") or resp.text
                except ValueError:
                    message = resp.text
                raise GitHubApiError(resp.status_code, message, request_id)
            return resp

        raise GitHubApiError(0, f"Network error after {DEFAULT_RETRY} attempts: {last_exc}")

    async def get_authenticated_login(self) -> str:
        if self._viewer_login:
            return self._viewer_login
        data = (await self._request("GET", "/user")).json()
        login = data.get("login")
        if not login:
            raise GitHubApiError(0, "Could not resolve authenticated GitHub login")
        self._viewer_login = str(login)
        return self._viewer_login

    async def resolve_owner_type(self, owner: str) -> str:
        cached = self._owner_type_cache.get(owner)
        if cached:
            return cached
        data = (await self._request("GET", f"/users/{owner}")).json()
        owner_type = "org" if data.get("type") == "Organization" else "user"
        self._owner_type_cache[owner] = owner_type
        return owner_type

    async def create_repo(
        self,
        owner: Optional[str],
        name: str,
        *,
        owner_type: str = "auto",
        private: bool = True,
        description: str = "",
        auto_init: bool = True,
    ) -> dict[str, Any]:
        resolved_owner_type = owner_type
        if resolved_owner_type == "auto":
            if owner:
                resolved_owner_type = await self.resolve_owner_type(owner)
            else:
                resolved_owner_type = "user"

        if resolved_owner_type == "user":
            viewer_login = await self.get_authenticated_login()
            if owner and owner != viewer_login:
                raise GitHubApiError(
                    400,
                    f"User-scoped repository creation can only target the authenticated user ({viewer_login}).",
                )
            resp = await self._request(
                "POST",
                "/user/repos",
                json={
                    "name": name,
                    "private": private,
                    "description": description,
                    "auto_init": auto_init,
                },
            )
            return resp.json()

        if not owner:
            raise GitHubApiError(400, "owner is required for org-scoped repository creation")
        resp = await self._request(
            "POST",
            f"/orgs/{owner}/repos",
            json={
                "name": name,
                "private": private,
                "description": description,
                "auto_init": auto_init,
            },
        )
        return resp.json()

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        return (await self._request("GET", f"/repos/{owner}/{repo}")).json()

    async def list_repos(
        self,
        owner: str,
        *,
        owner_type: str = "auto",
        per_page: int = 30,
    ) -> list[dict[str, Any]]:
        resolved_owner_type = owner_type if owner_type != "auto" else await self.resolve_owner_type(owner)
        if resolved_owner_type == "user":
            try:
                viewer_login = await self.get_authenticated_login()
            except GitHubApiError:
                viewer_login = ""
            if viewer_login and owner == viewer_login:
                return (await self._request("GET", "/user/repos", params={"per_page": per_page})).json()
            return (await self._request("GET", f"/users/{owner}/repos", params={"per_page": per_page})).json()
        return (await self._request("GET", f"/orgs/{owner}/repos", params={"per_page": per_page})).json()

    async def delete_repo(self, owner: str, repo: str) -> None:
        await self._request("DELETE", f"/repos/{owner}/{repo}")

    async def get_branch(self, owner: str, repo: str, branch: str) -> dict[str, Any]:
        return (await self._request("GET", f"/repos/{owner}/{repo}/branches/{branch}")).json()

    async def get_default_branch_sha(self, owner: str, repo: str) -> str:
        repo_data = await self.get_repo(owner, repo)
        default_branch = repo_data.get("default_branch")
        if not default_branch:
            raise GitHubApiError(0, f"Repository {owner}/{repo} has no default_branch")
        return (await self.get_branch(owner, repo, default_branch))["commit"]["sha"]

    async def create_branch(
        self,
        owner: str,
        repo: str,
        branch: str,
        *,
        from_branch: Optional[str] = None,
    ) -> dict[str, Any]:
        sha = (
            (await self.get_branch(owner, repo, from_branch))["commit"]["sha"]
            if from_branch
            else await self.get_default_branch_sha(owner, repo)
        )
        return (
            await self._request(
                "POST",
                f"/repos/{owner}/{repo}/git/refs",
                json={"ref": f"refs/heads/{branch}", "sha": sha},
            )
        ).json()

    async def get_file(
        self,
        owner: str,
        repo: str,
        path: str,
        *,
        ref: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        try:
            resp = await self._request(
                "GET",
                f"/repos/{owner}/{repo}/contents/{path}",
                params={"ref": ref} if ref else None,
            )
        except GitHubApiError as exc:
            if exc.status == 404:
                return None
            raise
        return resp.json()

    async def push_file(
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
            existing = await self.get_file(owner, repo, path, ref=branch)
            if existing and "sha" in existing:
                body["sha"] = existing["sha"]
        return (
            await self._request("PUT", f"/repos/{owner}/{repo}/contents/{path}", json=body)
        ).json()

    async def push_files(
        self,
        owner: str,
        repo: str,
        files: list[dict[str, str]],
        *,
        message: str,
        branch: str,
    ) -> dict[str, Any]:
        if not files:
            raise ValueError("files must not be empty")

        head = await self.get_branch(owner, repo, branch)
        head_sha = head["commit"]["sha"]
        commit = (await self._request("GET", f"/repos/{owner}/{repo}/git/commits/{head_sha}")).json()
        base_tree_sha = commit["tree"]["sha"]

        tree_items: list[dict[str, Any]] = []
        for file_def in files:
            blob = (
                await self._request(
                    "POST",
                    f"/repos/{owner}/{repo}/git/blobs",
                    json={"content": file_def["content"], "encoding": "utf-8"},
                )
            ).json()
            tree_items.append(
                {"path": file_def["path"], "mode": "100644", "type": "blob", "sha": blob["sha"]}
            )

        new_tree = (
            await self._request(
                "POST",
                f"/repos/{owner}/{repo}/git/trees",
                json={"base_tree": base_tree_sha, "tree": tree_items},
            )
        ).json()
        new_commit = (
            await self._request(
                "POST",
                f"/repos/{owner}/{repo}/git/commits",
                json={"message": message, "tree": new_tree["sha"], "parents": [head_sha]},
            )
        ).json()
        await self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
            json={"sha": new_commit["sha"]},
        )
        return new_commit
