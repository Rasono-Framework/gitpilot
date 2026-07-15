"""CLI subcommands.

Each command is a small function that takes argparse-parsed args plus a
``GitHubClient`` and an ``org`` default. We keep them together to avoid
creating one tiny file per command (overengineering for ~6 commands).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Callable

from .client import GitHubApiError, GitHubClient


CommandHandler = Callable[[argparse.Namespace, GitHubClient, str], int]


# ---------------------------------------------------------------- helpers
def _confirm(question: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _print_json(data) -> None:
    print(json.dumps(data, indent=2, sort_keys=True, default=str))


def _err(msg: str) -> None:
    print(f"[error] {msg}", file=sys.stderr)


# ---------------------------------------------------------------- commands
def cmd_create_repo(args: argparse.Namespace, client: GitHubClient, default_org: str) -> int:
    org = args.org or default_org
    if not args.name:
        _err("--name is required")
        return 2
    if not _confirm(
        f"Create {'private' if args.private else 'public'} repo "
        f"{org}/{args.name}?",
        assume_yes=args.yes,
    ):
        print("Aborted.")
        return 1
    try:
        repo = client.create_repo(
            org,
            args.name,
            private=args.private,
            description=args.description or "",
            auto_init=not args.no_init,
        )
    except GitHubApiError as exc:
        _err(f"create_repo failed: {exc}")
        return 1
    print(f"Created {repo['html_url']}")
    if args.json:
        _print_json(repo)
    return 0


def cmd_list_repos(args: argparse.Namespace, client: GitHubClient, default_org: str) -> int:
    org = args.org or default_org
    try:
        repos = client.list_repos(org, per_page=args.limit)
    except GitHubApiError as exc:
        _err(f"list_repos failed: {exc}")
        return 1
    if args.json:
        _print_json([{"name": r["name"], "private": r["private"], "html_url": r["html_url"]} for r in repos])
    else:
        for r in repos:
            visibility = "priv" if r["private"] else "publ"
            print(f"  {r['name']:40s}  [{visibility}]  {r['html_url']}")
    return 0


def cmd_delete_repo(args: argparse.Namespace, client: GitHubClient, default_org: str) -> int:
    org = args.org or default_org
    if not args.name:
        _err("--name is required")
        return 2
    if not _confirm(f"DELETE {org}/{args.name} (irreversible)?", assume_yes=args.yes):
        print("Aborted.")
        return 1
    try:
        client.delete_repo(org, args.name)
    except GitHubApiError as exc:
        _err(f"delete_repo failed: {exc}")
        return 1
    print(f"Deleted {org}/{args.name}")
    return 0


def cmd_create_branch(args: argparse.Namespace, client: GitHubClient, default_org: str) -> int:
    org = args.org or default_org
    if not args.repo or not args.branch:
        _err("--repo and --branch are required")
        return 2
    try:
        ref = client.create_branch(
            org, args.repo, args.branch, from_branch=args.from_branch
        )
    except GitHubApiError as exc:
        _err(f"create_branch failed: {exc}")
        return 1
    print(f"Created branch {args.branch} on {org}/{args.repo} (ref: {ref['ref']})")
    if args.json:
        _print_json(ref)
    return 0


def cmd_push_file(args: argparse.Namespace, client: GitHubClient, default_org: str) -> int:
    org = args.org or default_org
    if not args.repo or not args.branch or not args.path:
        _err("--repo, --branch, and --path are required")
        return 2

    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            content = fh.read()
    else:
        content = args.content or ""

    try:
        result = client.push_file(
            org,
            args.repo,
            args.path,
            content,
            message=args.message or f"Update {args.path}",
            branch=args.branch,
            update=not args.no_update,
        )
    except GitHubApiError as exc:
        _err(f"push_file failed: {exc}")
        return 1
    commit_sha = (result.get("commit") or {}).get("sha", "?")
    print(f"Pushed {args.path} -> {org}/{args.repo}@{args.branch} (commit {commit_sha[:7]})")
    if args.json:
        _print_json(result)
    return 0


def cmd_whoami(_args: argparse.Namespace, client: GitHubClient, _default_org: str) -> int:
    """Diagnostic: show the GitHub App's installations and confirm config.

    Uses the App-level JWT (not the installation token) so it works even when
    the configured installation ID is wrong. This is intentional: ``whoami``
    is the first thing you run when something doesn't work.
    """
    from .auth import GITHUB_API as _API
    import requests as _req

    app_jwt = client.auth._mint_app_jwt()  # noqa: SLF001
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    resp = _req.get(f"{_API}/app/installations", headers=headers, timeout=15)
    if not resp.ok:
        _err(f"whoami failed (App JWT rejected): {resp.status_code} {resp.text[:200]}")
        return 1

    installs = resp.json()
    configured_id = str(client.auth._installation_id)  # noqa: SLF001
    configured = next((i for i in installs if str(i.get("id")) == configured_id), None)

    print(f"GitHub App ID:        {client.auth._app_id}")  # noqa: SLF001
    print(f"Configured install:   {configured_id}")
    print(f"Total installations:  {len(installs)}")

    if configured is None:
        if installs:
            print("\nConfigured installation NOT found in this App's installations.")
            print("Available installations (use one of these IDs in GITHUB_INSTALLATION_ID):")
            for inst in installs:
                account = inst.get("account", {}) or {}
                print(
                    f"  - id={inst.get('id')}  account={account.get('login')}  "
                    f"type={account.get('type')}  status={inst.get('status')}"
                )
        else:
            print("\nThis GitHub App has no installations. Install it on an org or user first:")
            print("  https://github.com/apps/<your-app>/installations/new")
        return 0

    # Configured installation is valid — show what it can reach.
    print(f"\n✓ Installation {configured_id} is valid "
          f"(account={configured['account']['login']}, status={configured.get('status')})")

    # Try to list its repositories (requires installation token, but the App
    # can also call /installation/repositories with the App JWT by passing
    # the installation id in the URL).
    repo_resp = _req.get(
        f"{_API}/app/installations/{configured_id}/repositories",
        headers=headers,
        params={"per_page": 10},
        timeout=15,
    )
    if repo_resp.ok:
        data = repo_resp.json()
        total = data.get("total_count", 0)
        repos = data.get("repositories", [])
        print(f"Repositories accessible: {total} (showing up to 10):")
        for r in repos[:10]:
            print(f"  - {r['full_name']}  [{'priv' if r['private'] else 'publ'}]")
    else:
        print(f"(could not list repositories: {repo_resp.status_code})")

    rl = client.last_rate_limit
    if rl:
        print(f"\nRate limit: {rl.remaining}/{rl.limit} remaining")
    return 0


def cmd_list_installations(_args: argparse.Namespace, _client: GitHubClient, _default_org: str) -> int:
    """List every installation of the configured GitHub App.

    Uses the App-level JWT (not the installation token) because
    ``GET /app/installations`` is only reachable with App credentials.
    Helpful when GITHUB_INSTALLATION_ID is wrong or stale.
    """
    from .auth import GITHUB_API as _API
    import requests as _req

    # We need a fresh auth object because _client.auth may not have a config
    # attached (it does in practice, but we keep this command loosely coupled).
    auth = _client.auth
    app_jwt = auth._mint_app_jwt()  # noqa: SLF001
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    resp = _req.get(f"{_API}/app/installations", headers=headers, timeout=15)
    if not resp.ok:
        _err(f"list-installations failed: {resp.status_code} {resp.text[:200]}")
        return 1
    installs = resp.json()

    if _args.json:
        _print_json([
            {
                "id": inst.get("id"),
                "account": (inst.get("account") or {}).get("login"),
                "type": (inst.get("account") or {}).get("type"),
                "status": inst.get("status"),
            }
            for inst in installs
        ])
        return 0

    if not installs:
        print("No installations found for this GitHub App.")
        return 0

    print(f"Found {len(installs)} installation(s):")
    for inst in installs:
        account = inst.get("account", {}) or {}
        print(
            f"  - id={inst.get('id')}  account={account.get('login')}  "
            f"type={account.get('type')}  status={inst.get('status')}"
        )
    return 0


# --------------------------------------------------------------------- auth
def cmd_auth_login(_args: argparse.Namespace, _client: GitHubClient, _default_org: str) -> int:
    """Run the OAuth web flow and persist a user access token."""
    from . import oauth as _oauth
    from .config import get_oauth_config as _get_oauth
    import requests as _req  # local import for the network-error branch

    cfg = _get_oauth()
    if not cfg["client_id"] or not cfg["client_secret"]:
        _err(
            "Missing GITHUB_OAUTH_CLIENT_ID or GITHUB_OAUTH_CLIENT_SECRET in .env. "
            "Add both (you can find them in your GitHub App settings)."
        )
        return 2

    creds = _oauth.OAuthCredentials(
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        redirect_uri=cfg["redirect_uri"] or _oauth.DEFAULT_REDIRECT_URI,
    )

    try:
        stored = _oauth.login(creds)
    except (TimeoutError, RuntimeError, ValueError) as exc:
        _err(f"OAuth login failed: {exc}")
        return 1
    except _req.RequestException as exc:
        _err(f"Network error during OAuth: {exc}")
        return 1

    print(f"\n✓ Saved user token to {_oauth.token_file()} (mode 0600).")
    print(f"  Scope: {stored.scope or '(none reported)'}")
    # Sanity-check by calling /user.
    try:
        me = _oauth.fetch_authenticated_user(stored.access_token)
        print(f"  Authenticated as: {me.get('login')} ({me.get('html_url')})")
    except Exception as exc:  # noqa: BLE001
        print(f"  (could not verify with /user: {exc})")
    return 0


def cmd_auth_status(_args: argparse.Namespace, _client: GitHubClient, _default_org: str) -> int:
    """Show the persisted user token (if any) and verify it against /user."""
    from . import oauth as _oauth

    stored = _oauth.load_token()
    if stored is None:
        print("Not logged in. Run: python -m src auth login")
        return 0
    age_s = max(0.0, time.time() - stored.saved_at) if stored.saved_at else None
    print(f"Token file:    {_oauth.token_file()}")
    print(f"Saved:         {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stored.saved_at)) if stored.saved_at else 'unknown'}")
    if age_s is not None:
        print(f"Age:           {int(age_s // 86400)}d {int(age_s % 86400 // 3600)}h {int(age_s % 3600 // 60)}m")
    print(f"Scope:         {stored.scope or '(none reported)'}")
    print(f"Token prefix:  {stored.access_token[:8]}…")
    try:
        me = _oauth.fetch_authenticated_user(stored.access_token)
        print(f"Authenticated: {me.get('login')} ({me.get('html_url')})")
    except Exception as exc:  # noqa: BLE001
        print(f"Verification:  FAILED ({exc})")
        print("The token may have been revoked. Run: python -m src auth login")
        return 1
    return 0


def cmd_auth_logout(_args: argparse.Namespace, _client: GitHubClient, _default_org: str) -> int:
    """Delete the persisted user token."""
    from . import oauth as _oauth
    if _oauth.clear_token():
        print("Token removed.")
    else:
        print("No token to remove.")
    return 0


# ---------------------------------------------------------------- registration
def register_commands(sub: argparse._SubParsersAction) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--org", help="GitHub org (defaults to GITHUB_ORGANIZATION in .env)")
    common.add_argument("--json", action="store_true", help="Print raw JSON response")
    common.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts")

    p = sub.add_parser("whoami", parents=[common], help="Show installation identity & reachable repos")
    p.set_defaults(func=cmd_whoami)

    # ---- auth subcommand group ----
    p = sub.add_parser("auth-login", help="Authenticate via OAuth (browser-based, one-time)")
    p.set_defaults(func=cmd_auth_login)

    p = sub.add_parser("auth-status", help="Show the currently stored user token and verify it")
    p.set_defaults(func=cmd_auth_status)

    p = sub.add_parser("auth-logout", help="Delete the stored user token")
    p.set_defaults(func=cmd_auth_logout)

    p = sub.add_parser(
        "list-installations",
        parents=[common],
        help="List all installations of the configured GitHub App (uses App JWT)",
    )
    p.set_defaults(func=cmd_list_installations)

    p = sub.add_parser("create-repo", parents=[common], help="Create a repository in the org")
    p.add_argument("--name", required=True, help="Repository name")
    p.add_argument("--description", default="", help="Short description")
    p.add_argument("--private", action="store_true", default=True, help="Make it private (default)")
    p.add_argument("--public", dest="private", action="store_false", help="Make it public")
    p.add_argument("--no-init", action="store_true", help="Do not create an initial commit")
    p.set_defaults(func=cmd_create_repo)

    p = sub.add_parser("list-repos", parents=[common], help="List org repositories")
    p.add_argument("--limit", type=int, default=30, help="Max results (default 30)")
    p.set_defaults(func=cmd_list_repos)

    p = sub.add_parser("delete-repo", parents=[common], help="Delete a repository (irreversible)")
    p.add_argument("--name", required=True, help="Repository name")
    p.set_defaults(func=cmd_delete_repo)

    p = sub.add_parser("create-branch", parents=[common], help="Create a branch in a repo")
    p.add_argument("--repo", required=True, help="Repository name")
    p.add_argument("--branch", required=True, help="New branch name")
    p.add_argument("--from-branch", help="Source branch (default: repo's default branch)")
    p.set_defaults(func=cmd_create_branch)

    p = sub.add_parser("push-file", parents=[common], help="Create or update a file in a repo")
    p.add_argument("--repo", required=True, help="Repository name")
    p.add_argument("--branch", required=True, help="Target branch")
    p.add_argument("--path", required=True, help="File path inside the repo")
    p.add_argument("--file", help="Read content from this local file (mutually exclusive with --content)")
    p.add_argument("--content", help="Inline content (mutually exclusive with --file)")
    p.add_argument("--message", help="Commit message (default: 'Update <path>')")
    p.add_argument("--no-update", action="store_true", help="Fail if file already exists (no upsert)")
    p.set_defaults(func=cmd_push_file)
