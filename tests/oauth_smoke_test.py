"""End-to-end test of the OAuth flow with a mocked browser + token endpoint.

This proves the CLI's OAuth path works without ever touching the network. We:
  1. Mock ``webbrowser.open`` to immediately fire a fake callback at the
     local server (simulating the user authorizing).
  2. Mock the POST to ``/login/oauth/access_token`` to return a fake token.
  3. Verify the token was saved to disk with the right shape and permissions.

Run with:
    .venv/bin/python tests/oauth_smoke_test.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Force the token file to a temp path BEFORE importing oauth.
TEST_HOME = PROJECT_ROOT / ".tmp_test_home"
TEST_HOME.mkdir(exist_ok=True)
os.environ["GH_API_CLI_TOKEN_DIR"] = str(TEST_HOME / "gh-api-cli")

from src import oauth  # noqa: E402


def _fake_browser_callback(redirect_uri: str, state: str) -> None:
    """Simulate the user clicking 'Authorize' in the browser.

    Fires a GET to the local callback with a fake code. Runs in a thread so
    it doesn't block the test driver.
    """
    def _fire():
        time.sleep(0.1)  # give the server a moment to start
        params = {"code": "fake_auth_code_abc123", "state": state}
        url = f"{redirect_uri}?{urllib.parse.urlencode(params)}"
        try:
            urllib.request.urlopen(url, timeout=5).read()
        except Exception as exc:  # noqa: BLE001
            print(f"  (callback fire error: {exc})")

    threading.Thread(target=_fire, daemon=True).start()
    return True  # webbrowser.open returns True to indicate "opened"


def _fake_token_exchange(*args, **kwargs):
    """Mock requests.post for the access_token exchange."""
    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "access_token": "gho_fake_user_token_xyz",
                "scope": "repo,workflow",
                "token_type": "bearer",
            }

    return _Resp()


def main() -> int:
    print("=== OAuth smoke test ===\n")

    # Sanity: start with no token.
    assert oauth.load_token() is None, "token file should not exist yet"
    print("[1/5] Pre-condition: no token file ✓\n")

    creds = oauth.OAuthCredentials(
        client_id="Iv23liKe9yPTmWU9CM6A",
        client_secret="<test-secret>",
        redirect_uri="http://127.0.0.1:8765/callback",
    )

    # The opener captures the authorize URL and the state, then fires the
    # callback on a separate thread to simulate the user.
    captured: dict[str, str] = {}

    def fake_opener(url: str) -> bool:
        captured["url"] = url
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        captured["state"] = qs["state"][0]
        _fake_browser_callback(creds.redirect_uri, captured["state"])
        return True

    print("[2/5] Running OAuth flow (mocked browser + token exchange)…")
    with patch.object(oauth.requests, "post", side_effect=_fake_token_exchange):
        stored = oauth.login(creds, open_browser=fake_opener, timeout_seconds=10)
    print(f"      authorize URL: {captured.get('url', '?')[:90]}…")
    print(f"      got token:     {stored.access_token[:14]}…")
    print(f"      scope:         {stored.scope}\n")

    assert stored.access_token == "gho_fake_user_token_xyz", stored
    assert stored.scope == "repo,workflow", stored
    assert captured["url"].startswith("https://github.com/login/oauth/authorize"), captured

    # Verify the token file was created with the right perms.
    print("[3/5] Verifying token file on disk…")
    path = oauth.token_file()
    assert path.exists(), f"{path} should exist"
    mode = path.stat().st_mode & 0o777
    print(f"      path:  {path}")
    print(f"      mode:  {oct(mode)} (expect 0o600)")
    assert mode == 0o600, f"token file mode is {oct(mode)}, want 0o600"
    print("      ✓ file exists and is 0o600\n")

    # Verify load_token reads it back correctly.
    print("[4/5] Re-loading token from disk…")
    loaded = oauth.load_token()
    assert loaded is not None
    assert loaded.access_token == stored.access_token
    assert loaded.scope == stored.scope
    print(f"      ✓ loaded back: prefix={loaded.access_token[:8]}…\n")

    # Verify the token is consumed by the CLI (via _resolve_user_token).
    print("[5/5] Simulating CLI token resolution…")
    # We don't have a .env, so load_config would fail. Test the oauth path
    # directly by writing the token where the CLI looks.
    from src.cli import _resolve_user_token
    resolved = _resolve_user_token()
    assert resolved == stored.access_token, (resolved, stored.access_token)
    print(f"      ✓ CLI would use prefix={resolved[:8]}…\n")

    # Cleanup: remove the token file so the test is idempotent.
    oauth.clear_token()
    print("Cleanup: token file removed.\n")
    print("=== All OAuth checks passed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
