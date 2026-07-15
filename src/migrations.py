"""Alembic integration for gitpilot."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from .config import Config


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def build_alembic_config(app_config: Config) -> AlembicConfig:
    root = _project_root()
    alembic_cfg = AlembicConfig(str(root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(root / "migrations"))
    alembic_cfg.set_main_option("sqlalchemy.url", app_config.database_url)
    return alembic_cfg


def upgrade_to_head(app_config: Config) -> None:
    if app_config.state_backend != "sql":
        return
    command.upgrade(build_alembic_config(app_config), "head")


def current_revision(app_config: Config, verbose: bool = False) -> None:
    if app_config.state_backend != "sql":
        return
    command.current(build_alembic_config(app_config), verbose=verbose)
