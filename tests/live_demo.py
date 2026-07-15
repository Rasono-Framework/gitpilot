"""Live demo: spin up a fake GitHub server, point the CLI at it, exercise
create-repo → create-branch → push-file end-to-end.

This runs the **real** CLI code paths against a local HTTP server that
fakes just enough of the GitHub API. The CLI is not unit-tested — it's
actually invoked. We can do this because:
  * the App has no real installation (0 from ``list-installations``), so
    we can't run against api.github.com;
  * the CLI accepts a ``base_url`` override (handy for GHES too);
  * the request shapes are stable, so a focused mock is enough to prove
    the contract.

Run with:
    .venv/bin/python tests/live_demo.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# --- Bypass real config: provide the env vars the loader expects, but
# point at our fake server.
os.environ["GITHUB_APP_ID"] = "4248933"
os.environ["GITHUB_PRIVATE_KEY"] = "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----"
os.environ["GITHUB_INSTALLATION_ID"] = "99999999"
os.environ["GITHUB_ORGANIZATION"] = "orvyx"
os.environ["GITHUB_OAUTH_CLIENT_ID"] = "Iv23liKe9yPTmWU9CM6A"
os.environ["GITHUB_OAUTH_CLIENT_SECRET"] = "fake"
# Point the local env file to nothing; load_config() will read env vars above.
os.environ["GITHUB_ENV_FILE"] = str(PROJECT_ROOT / ".env.does_not_exist")

# Provide a pre-minted user token to skip the App auth path entirely.
os.environ["GITHUB_USER_TOKEN"] = "gho_fake_user_token"

# --- Bypass JWT signing by pre-populating the cache. The fake server
# doesn't actually verify the JWT, but PyJWT will choke on our fake key.
from src.auth import _CachedToken  # noqa: E402

# --- Start the fake server.
import fake_github as _fake

server, BASE_URL = _fake.start(port=0)
print(f"=== Fake GitHub server listening at {BASE_URL} ===\n")


def main() -> int:
    # Build the same stack the CLI builds, but pointed at the fake server.
    from src.auth import GitHubAppAuth
    from src.client import GitHubClient
    from src.config import load_config

    config = load_config()
    auth = GitHubAppAuth(config)
    # Pre-fill the installation-token cache so the client doesn't try to
    # validate our fake JWT against the real auth module.
    auth._cached = _CachedToken(token="ghs_fake", expires_at=time.time() + 3600)

    user_token = os.environ["GITHUB_USER_TOKEN"]
    client = GitHubClient(auth, user_token=user_token, base_url=BASE_URL)

    print("[1/4] create_repo  orvyx/demo-live-action  (private)")
    repo = client.create_repo("orvyx", "demo-live-action", private=True,
                              description="Created via gh-api-cli live demo")
    print(f"      → {repo['html_url']}  (default branch: {repo['default_branch']})\n")

    print("[2/4] create_branch  feat/api  (from main)")
    ref = client.create_branch("orvyx", "demo-live-action", "feat/api", from_branch="main")
    print(f"      → {ref['ref']}  (sha: {ref['object']['sha'][:10]}…)\n")

    print("[3/4] push_file  README.md  (on feat/api)")
    content = "# demo-live-action\n\nCreated by the gh-api-cli live demo.\n"
    res = client.push_file(
        "orvyx", "demo-live-action", "README.md", content,
        message="docs: add README via API",
        branch="feat/api",
    )
    print(f"      → commit {res['commit']['sha'][:10]}… pushed at {res['content']['path']}\n")

    print("[4/4] push_files  (atomic multi-file commit)")
    files = [
        {"path": "src/main.py", "content": 'def main():\n    print("hi from API")\n'},
        {"path": ".gitignore", "content": "__pycache__/\n*.pyc\n"},
        {"path": "tests/test_main.py", "content": "from src.main import main\n\n\ndef test_main(capsys):\n    main()\n    assert capsys.readouterr().out.strip() == \"hi from API\"\n"},
    ]
    commit = client.push_files(
        "orvyx", "demo-live-action", files,
        message="feat: bootstrap project (3 files in 1 commit)",
        branch="feat/api",
    )
    print(f"      → commit {commit['sha'][:10]}…  ({len(files)} files in one commit)\n")

    print("=== Live demo complete ===")
    print(f"(Fake server still running on {BASE_URL}; auto-shutting down in 2s.)")
    # Don't block on input in this CI-style run; shut down after 2s.
    server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
