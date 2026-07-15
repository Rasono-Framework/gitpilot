"""OAuth web flow for GitHub Apps (user-to-server tokens).

This complements the App-level JWT flow in ``auth.py``. The OAuth flow:
  1. Generates a CSRF ``state`` token.
  2. Starts a tiny local HTTP server on the host:port of the redirect URI.
  3. Opens the user's browser to GitHub's authorize URL.
  4. Waits for the redirect callback carrying the ``code``.
  5. Exchanges the code for a user access token (``gho_...``).
  6. Persists the token to ``~/.config/gh-api-cli/token.json`` (mode 0600).

The user only does this once; after that, every CLI command picks up the
token from disk and uses it as a Bearer header. No more browser interaction.

Security notes:
  * The token file is created with mode 0600 (owner read/write only).
  * The state token is verified on the callback to block CSRF.
  * The local server binds to 127.0.0.1, not 0.0.0.0 — no exposure to LAN.
  * If the user has set a non-localhost redirect URI (e.g. a zrok share),
    the localhost server cannot catch the callback; we fail fast with a
    clear message rather than silently waiting forever.
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from .auth import GITHUB_API

logger = logging.getLogger(__name__)

AUTH_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
FLOW_TIMEOUT_SECONDS = 5 * 60  # 5 minutes — generous for human interaction


# ----------------------------------------------------------------- persistence
def token_dir() -> Path:
    """Return the directory where the user token is stored. Created if missing."""
    base = Path(os.environ.get("GH_API_CLI_TOKEN_DIR", Path.home() / ".config" / "gh-api-cli"))
    base.mkdir(parents=True, exist_ok=True)
    # Best-effort permission tightening; on Windows the chmod is a no-op.
    try:
        base.chmod(0o700)
    except OSError:
        pass
    return base


def token_file() -> Path:
    return token_dir() / "token.json"


@dataclass
class StoredToken:
    access_token: str
    scope: str = ""
    token_type: str = "bearer"
    saved_at: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> "StoredToken":
        return cls(
            access_token=data["access_token"],
            scope=data.get("scope", ""),
            token_type=data.get("token_type", "bearer"),
            saved_at=float(data.get("saved_at", 0.0)),
        )

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "scope": self.scope,
            "token_type": self.token_type,
            "saved_at": self.saved_at,
        }


def load_token() -> Optional[StoredToken]:
    """Read the persisted user token, or return None if absent/invalid."""
    path = token_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return StoredToken.from_dict(data)
    except (json.JSONDecodeError, KeyError, OSError) as exc:
        logger.warning("could not read token file %s: %s", path, exc)
        return None


def save_token(stored: StoredToken) -> Path:
    """Persist the user token to disk with 0600 permissions."""
    path = token_file()
    path.write_text(json.dumps(stored.to_dict(), indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def clear_token() -> bool:
    """Remove the token file. Returns True if a file was deleted."""
    path = token_file()
    if path.exists():
        path.unlink()
        return True
    return False


# -------------------------------------------------------------- OAuth web flow
@dataclass
class OAuthCredentials:
    client_id: str
    client_secret: str
    redirect_uri: str = DEFAULT_REDIRECT_URI


def _parse_redirect_uri(uri: str) -> tuple[str, int, str]:
    """Return (host, port, path) from a redirect URI, with safe defaults."""
    parsed = urllib.parse.urlparse(uri)
    host = parsed.hostname or "127.0.0.1"
    # Bind to loopback only — never expose the auth callback to the LAN.
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(
            f"Redirect URI host {host!r} is not loopback. The CLI's local "
            "callback server can only bind to 127.0.0.1. Either change "
            "GITHUB_OAUTH_REDIRECT_URI to a localhost URL (and add it to the "
            "App's callback whitelist), or run a backend that proxies the "
            "callback to the CLI."
        )
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/callback"
    return host, port, path


def _exchange_code(
    creds: OAuthCredentials, code: str, timeout: float = 15.0
) -> dict:
    """Trade the authorization code for an access token."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "code": code,
            "redirect_uri": creds.redirect_uri,
        },
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Token exchange returned no access_token: {data!r}")
    return data


def login(
    creds: OAuthCredentials,
    *,
    open_browser: Optional[Callable[[str], bool]] = None,
    timeout_seconds: int = FLOW_TIMEOUT_SECONDS,
) -> StoredToken:
    """Run the full OAuth web flow and return the stored token.

    Args:
        creds: Client ID, client secret, and redirect URI.
        open_browser: Callable that opens a URL in a browser. Defaults to
            ``webbrowser.open``. Tests pass a no-op.
        timeout_seconds: Max time to wait for the user to authorize.
    """
    host, port, path = _parse_redirect_uri(creds.redirect_uri)
    state = secrets.token_urlsafe(24)

    captured: dict[str, str] = {}
    error: dict[str, str] = {}
    done = threading.Event()

    SUCCESS_HTML = (
        b"<!doctype html><html><body style=\"font-family:system-ui;padding:48px;\">"
        b"<h1>Authorization complete</h1>"
        b"<p>You can close this tab and return to your terminal.</p>"
        b"</body></html>"
    )

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
            if not self.path.startswith(path):
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "error" in qs:
                error["error"] = (
                    qs.get("error_description", qs.get("error", ["unknown"]))[0]
                )
            else:
                captured["code"] = (qs.get("code") or [None])[0] or ""
                captured["state"] = (qs.get("state") or [None])[0] or ""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(SUCCESS_HTML)))
            self.end_headers()
            self.wfile.write(SUCCESS_HTML)
            done.set()

        def log_message(self, *args, **kwargs):  # noqa: ANN001
            return  # suppress access logs

    server = http.server.HTTPServer((host, port), _Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    opener = open_browser or webbrowser.open
    try:
        auth_url = AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": creds.client_id,
            "redirect_uri": creds.redirect_uri,
            "state": state,
            "allow_signup": "false",
        })
        print(f"Opening browser to:\n  {auth_url}\n")
        opened = opener(auth_url)
        if not opened:
            print(
                "Browser did not open automatically. Please paste the URL above "
                "into your browser to continue."
            )

        print(f"Waiting for authorization (timeout: {timeout_seconds // 60} min)…")
        if not done.wait(timeout=timeout_seconds):
            raise TimeoutError(
                f"OAuth flow timed out after {timeout_seconds} seconds. "
                "Restart with `auth login` when ready."
            )

        if error:
            raise RuntimeError(f"OAuth authorization failed: {error['error']}")

        code = captured.get("code", "")
        if not code:
            raise RuntimeError("Authorization callback contained no code.")
        if captured.get("state") != state:
            raise RuntimeError(
                "State token mismatch — possible CSRF. Aborting."
            )

        token_data = _exchange_code(creds, code)
        stored = StoredToken(
            access_token=token_data["access_token"],
            scope=token_data.get("scope", ""),
            token_type=token_data.get("token_type", "bearer"),
            saved_at=time.time(),
        )
        save_token(stored)
        return stored
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------- authenticated user
def fetch_authenticated_user(access_token: str, timeout: float = 15.0) -> dict:
    """Return ``GET /user`` for the given OAuth token. Useful for verification."""
    resp = requests.get(
        f"{GITHUB_API}/user",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()
