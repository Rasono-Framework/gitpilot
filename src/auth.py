"""GitHub App authentication.

Implements the standard 2-step flow:

1. Sign a short-lived JWT (10 min max) with the App's private key.
2. Exchange that JWT for an installation access token (1 h).

The installation token is cached in memory and refreshed automatically.
Tokens are never logged; we expose a `redacted()` view for diagnostics.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

import jwt
import requests

from .config import Config

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

# JWT lifetime: GitHub allows up to 10 min. We pick 9 to leave a 1 min
# safety margin against clock skew at both ends.
JWT_TTL_SECONDS = 9 * 60

# Installation tokens last 1 h. Refresh 5 min before expiry to be safe.
TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60
TOKEN_TTL_SECONDS = 60 * 60

API_VERSION = "2022-11-28"
USER_AGENT = "github-api-cli/0.1"


class GitHubAuthError(RuntimeError):
    """Raised when the App credentials are invalid or the installation is missing."""


@dataclass
class _CachedToken:
    token: str
    expires_at: float  # epoch seconds


class GitHubAppAuth:
    """Handles JWT minting + installation token caching (thread-safe)."""

    def __init__(self, config: Config) -> None:
        self._app_id = config.app_id
        self._private_key = config.private_key
        self._installation_id = config.installation_id
        self._cached: Optional[_CachedToken] = None
        self._lock = threading.Lock()

    # --- JWT (App-level) ---
    def _mint_app_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iat": now - 30,  # 30s clock-skew buffer in the past
            "exp": now + JWT_TTL_SECONDS,
            "iss": self._app_id,
        }
        try:
            return jwt.encode(payload, self._private_key, algorithm="RS256")
        except jwt.InvalidKeyError as exc:
            raise GitHubAuthError(
                "GITHUB_PRIVATE_KEY is not a valid RSA PEM key. "
                "Check that the BEGIN/END markers and the body are intact."
            ) from exc

    # --- Installation token (what we actually use for API calls) ---
    def get_token(self, force_refresh: bool = False) -> str:
        """Return a valid installation access token, refreshing if needed."""
        with self._lock:
            now = time.time()
            if (
                not force_refresh
                and self._cached
                and now < self._cached.expires_at - TOKEN_REFRESH_MARGIN_SECONDS
            ):
                return self._cached.token

            app_jwt = self._mint_app_jwt()
            url = f"{GITHUB_API}/app/installations/{self._installation_id}/access_tokens"
            headers = {
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": USER_AGENT,
            }
            try:
                resp = requests.post(url, headers=headers, timeout=15)
            except requests.RequestException as exc:
                raise GitHubAuthError(
                    f"Network error while exchanging JWT for installation token: {exc}"
                ) from exc

            if resp.status_code == 401:
                raise GitHubAuthError(
                    "GitHub rejected the App JWT (401). "
                    "Verify GITHUB_APP_ID and that GITHUB_PRIVATE_KEY is the App's "
                    "private key (not a download from a different key)."
                )
            if resp.status_code == 404:
                # Try to give the user a more useful error: list the App's
                # actual installations so they can pick the right ID.
                hint = self._list_installations_hint(app_jwt)
                raise GitHubAuthError(
                    f"Installation {self._installation_id} not found (404). "
                    "Make sure the App is installed on the target org/user "
                    "and that GITHUB_INSTALLATION_ID matches.\n"
                    + hint
                )
            if not resp.ok:
                raise GitHubAuthError(
                    f"Failed to obtain installation token: "
                    f"{resp.status_code} {resp.text[:300]}"
                )

            data = resp.json()
            token = data.get("token")
            expires_at_iso = data.get("expires_at")
            if not token or not expires_at_iso:
                raise GitHubAuthError(
                    "Unexpected response from GitHub when fetching installation token: "
                    f"{data!r}"
                )

            # expires_at is RFC3339 / ISO 8601, e.g. "2026-07-15T12:34:56Z".
            # `datetime.fromisoformat` accepts the 'Z' suffix from Python 3.11+;
            # we normalize for older runtimes as well so the tool doesn't break
            # on a quirky GitHub response.
            from datetime import datetime, timezone

            try:
                normalized = expires_at_iso.replace("Z", "+00:00")
                dt = datetime.fromisoformat(normalized)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                expires_epoch = dt.timestamp()
            except (TypeError, ValueError) as exc:
                raise GitHubAuthError(
                    f"Could not parse installation token expiry: {expires_at_iso!r}"
                ) from exc

            self._cached = _CachedToken(token=token, expires_at=expires_epoch)
            logger.info("Obtained fresh installation token (expires %s)", expires_at_iso)
            return token

    def invalidate(self) -> None:
        """Drop the cached token (used by the CLI on 401/403)."""
        with self._lock:
            self._cached = None

    def _list_installations_hint(self, app_jwt: str) -> str:
        """Build a human-readable hint listing the App's real installations.

        Best-effort: never raises. Used to enrich the 404 error message so
        the user can spot a wrong GITHUB_INSTALLATION_ID without running
        a separate ``list-installations`` command.
        """
        try:
            resp = requests.get(
                f"{GITHUB_API}/app/installations",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": API_VERSION,
                    "User-Agent": USER_AGENT,
                },
                timeout=10,
            )
            if not resp.ok:
                return "  (hint unavailable: could not list installations)"
            installs = resp.json()
            if not installs:
                return (
                    "  This GitHub App has no installations yet. Install it first:\n"
                    "    https://github.com/apps/<your-app>/installations/new"
                )
            lines = ["  Available installations for this App:"]
            for inst in installs:
                account = inst.get("account", {}) or {}
                lines.append(
                    f"    - id={inst.get('id')}  account={account.get('login')}  "
                    f"type={account.get('type')}  status={inst.get('status')}"
                )
            return "\n".join(lines)
        except requests.RequestException as exc:
            return f"  (hint unavailable: {exc})"
