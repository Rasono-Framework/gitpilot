"""CLI entrypoint: ``python -m src`` or the ``github-api`` console script."""

from __future__ import annotations

import argparse
import logging
import sys

from .auth import GitHubAppAuth, GitHubAuthError
from .client import GitHubClient
from .commands import register_commands
from .config import load_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github-api",
        description="Drive the GitHub REST API via a GitHub App installation.",
        epilog="Config is loaded from a .env file in the current directory.",
    )
    parser.add_argument(
        "--verbose", "-v", action="count", default=0,
        help="Increase logging verbosity (-v, -vv)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    register_commands(sub)
    return parser


def _resolve_user_token() -> str | None:
    """Return the OAuth user token from env var or disk, or None.

    Priority: ``GITHUB_USER_TOKEN`` env var > persisted token file. The
    persisted file is what ``auth login`` writes. We never require it —
    when absent, the CLI falls back to the App-level JWT flow.
    """
    import os
    env = os.environ.get("GITHUB_USER_TOKEN", "").strip()
    if env:
        return env
    try:
        from . import oauth as _oauth
        stored = _oauth.load_token()
        return stored.access_token if stored else None
    except Exception:  # noqa: BLE001 - never let token load crash the CLI
        return None


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # Logging level: quiet by default, -v = INFO, -vv = DEBUG.
    level = logging.WARNING - 10 * min(args.verbose, 2)
    logging.basicConfig(
        level=max(level, logging.DEBUG),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config()
    except (FileNotFoundError, EnvironmentError) as exc:
        print(f"[config] {exc}", file=sys.stderr)
        return 2

    try:
        auth = GitHubAppAuth(config)
        user_token = _resolve_user_token()
        client = GitHubClient(auth, user_token=user_token)
        # Log the resolved auth mode (without leaking the token).
        if user_token:
            logger = logging.getLogger(__name__)
            logger.info("using OAuth user token (prefix=%s…)", user_token[:6])
        return args.func(args, client, config.organization)
    except GitHubAuthError as exc:
        print(f"[auth] {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
