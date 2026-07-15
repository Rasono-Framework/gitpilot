"""End-to-end smoke test of the GitHubClient with a mocked HTTP layer.

We don't hit the real GitHub API: the user's App currently has no installation,
which is a config issue, not a code issue. This script proves the code paths
work by simulating GitHub responses for the full create-repo → create-branch
→ push-file flow.

Run with:
    .venv/bin/python tests/smoke_test.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Allow `import src` when running from the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.auth import GitHubAppAuth  # noqa: E402
from src.client import GitHubClient  # noqa: E402
from src.config import Config  # noqa: E402


# A tiny, in-process replacement for requests.Session that returns canned
# responses based on (method, path). No network I/O.
class FakeResponse:
    def __init__(self, status: int, json_body=None, text: str = ""):
        self.status_code = status
        self._json = json_body
        self.text = text if text else (json.dumps(json_body) if json_body is not None else "")
        self.headers = {
            "x-ratelimit-remaining": "4999",
            "x-ratelimit-limit": "5000",
            "x-ratelimit-reset": "0",
        }

    def ok(self) -> bool:  # not used by Session, kept for clarity
        return 200 <= self.status_code < 300

    def json(self):
        return self._json

    def raise_for_status(self):
        if not self.ok():
            raise RuntimeError(f"{self.status_code}: {self.text}")


def fake_session():
    """Return a MagicMock standing in for requests.Session, with routing."""
    session = MagicMock()

    def route(method: str, url: str, **kwargs):
        method = method.upper()
        print(f"  → {method} {url}")
        if method == "POST" and url.endswith("/access_tokens"):
            return FakeResponse(201, {
                "token": "ghs_fake_installation_token",
                "expires_at": "2030-01-01T00:00:00Z",
            })
        if method == "POST" and url.endswith("/repos") and "/installations/" not in url:
            return FakeResponse(201, {
                "name": kwargs["json"]["name"],
                "private": kwargs["json"]["private"],
                "html_url": f"https://github.com/orvyx/{kwargs['json']['name']}",
                "default_branch": "main",
                "full_name": f"orvyx/{kwargs['json']['name']}",
            })
        if method == "GET" and url.endswith("/branches/main"):
            return FakeResponse(200, {"commit": {"sha": "deadbeef" * 5}})
        if method == "POST" and url.endswith("/git/refs"):
            return FakeResponse(201, {
                "ref": kwargs["json"]["ref"],
                "object": {"sha": "deadbeef" * 5},
            })
        if method == "GET" and "/contents/" in url and url.endswith("README.md"):
            return FakeResponse(404)  # file does not exist → create
        if method == "PUT" and "/contents/" in url:
            return FakeResponse(201, {
                "commit": {"sha": "newshas" + "a" * 30},
                "content": {"path": kwargs["json"].get("path", "?")},
            })
        return FakeResponse(404, {"message": f"unmocked: {method} {url}"})

    session.request.side_effect = lambda method, url, **kw: route(method, url, **kw)
    return session


def main() -> int:
    print("=== Smoke test: create_repo → create_branch → push_file ===\n")

    cfg = Config(
        app_id="4248933",
        private_key="placeholder",  # not used — we mock the JWT
        installation_id="98765432",
        organization="orvyx",
        env_path=Path("(test)"),
    )
    auth = GitHubAppAuth(cfg)
    # Skip the real JWT signing by pre-populating the cache.
    from src.auth import _CachedToken
    import time
    auth._cached = _CachedToken(token="ghs_fake_installation_token", expires_at=time.time() + 3600)

    client = GitHubClient(auth, session=fake_session())

    print("[1/3] create_repo")
    repo = client.create_repo("orvyx", "smoke-test-repo", private=True, description="smoke test")
    assert repo["name"] == "smoke-test-repo", repo
    assert repo["html_url"].endswith("/smoke-test-repo"), repo
    print(f"      ✓ created {repo['html_url']}\n")

    print("[2/3] create_branch")
    ref = client.create_branch("orvyx", "smoke-test-repo", "feat/api", from_branch="main")
    assert ref["ref"] == "refs/heads/feat/api", ref
    print(f"      ✓ created {ref['ref']}\n")

    print("[3/3] push_file")
    result = client.push_file(
        "orvyx",
        "smoke-test-repo",
        "README.md",
        "# Hello from gh-api-cli\n",
        message="add README via API",
        branch="feat/api",
    )
    assert result["commit"]["sha"].startswith("newshas"), result
    print(f"      ✓ committed {result['commit']['sha'][:7]} on feat/api\n")

    print("=== All smoke checks passed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
