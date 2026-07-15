"""Server entrypoint for the FastAPI backend."""

from __future__ import annotations

import argparse

import uvicorn

from .config import load_config
from .migrations import current_revision, upgrade_to_head


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="github-api-server")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="Run the FastAPI server")
    sub.add_parser("init-db", help="Apply Alembic migrations up to head")
    sub.add_parser("db-current", help="Show the current Alembic revision")
    return parser


def _init_db() -> int:
    config = load_config()
    upgrade_to_head(config)
    return 0


def _db_current() -> int:
    config = load_config()
    current_revision(config, verbose=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "init-db":
        return _init_db()
    if args.command == "db-current":
        return _db_current()

    config = load_config()
    uvicorn.run(
        "src.api_app:create_app",
        factory=True,
        host=config.api_host,
        port=config.api_port,
        workers=config.api_workers,
        proxy_headers=True,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
