"""Server entrypoint for the FastAPI backend."""

from __future__ import annotations

import argparse
import asyncio

import uvicorn

from .api_db import Database
from .config import load_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="github-api-server")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="Run the FastAPI server")
    sub.add_parser("init-db", help="Create the database schema")
    return parser


async def _init_db() -> int:
    config = load_config()
    if config.state_backend != "sql":
        return 0
    db = Database(config)
    try:
        await db.create_schema()
    finally:
        await db.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "init-db":
        return asyncio.run(_init_db())

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
