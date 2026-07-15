"""Environment / .env loader.

Centralizes how configuration reaches the rest of the app, normalizes the
private key, and refuses to start with a missing or partially-defined config.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path


REQUIRED_KEYS = (
    "GITHUB_APP_ID",
    "GITHUB_PRIVATE_KEY",
    "GITHUB_INSTALLATION_ID",
)

# Files we will look for, in order, relative to CWD first, then project root.
ENV_CANDIDATES = (".env", ".ENV", ".env.local")


@dataclass(frozen=True)
class Config:
    app_id: str
    private_key: str
    installation_id: str
    env_path: Path
    organization: str = ""
    api_auth_token: str = ""
    state_backend: str = "sql"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 1
    github_api_base_url: str = "https://api.github.com"
    github_timeout_seconds: float = 15.0
    github_user_token: str = ""
    database_url: str = "sqlite+aiosqlite:///./github_api.db"
    db_echo: bool = False
    db_pool_size: int = 20
    db_max_overflow: int = 40
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800
    queue_maxsize: int = 10000
    queue_workers: int = 64


def _find_env_file_or_none() -> Path | None:
    cwd = Path.cwd()
    project_root = Path(__file__).resolve().parent.parent
    for base in (cwd, project_root):
        for name in ENV_CANDIDATES:
            candidate = base / name
            if candidate.is_file():
                return candidate
    return None


# A key line is the first KEY on a physical line. Continuation lines (no '='
# at the start) extend the previous value, which is how we support unquoted
# multi-line PEM blocks inside a .env file.
_KEY_RE = re.compile(r"^[ \t]*([A-Z_][A-Z0-9_]*)[ \t]*=[ \t]*(.*)$")


def _parse_env(path: Path) -> dict[str, str]:
    """Parse a .env file with support for unquoted multi-line values.

    Rules:
      * ``KEY=value`` starts an entry.
      * Lines without a ``=`` continue the previous value (real newlines).
      * ``"..."`` and ``'...'`` wrapped values have their quotes stripped.
      * ``#`` and blank lines outside of a value are ignored.
      * Within a value, ``#`` is treated as literal text (we do not support
        inline comments — keeping the parser tiny and predictable).
    """
    result: dict[str, str] = {}
    current_key: str | None = None
    current_parts: list[str] = []

    def _commit() -> None:
        if current_key is None:
            return
        raw = "\n".join(current_parts)
        # Strip the trailing newline artifact that a final continuation line
        # would leave if the file ends with the value.
        raw = raw.rstrip("\n")
        # Quote stripping (only when the whole value is wrapped in matching
        # quotes — no escape handling, this is a .env, not a shell script).
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
            raw = raw[1:-1]
        result[current_key] = raw  # type: ignore[index]

    for line in path.read_text(encoding="utf-8").splitlines():
        m = _KEY_RE.match(line)
        if m:
            # New key: commit the previous one first.
            _commit()
            current_key = m.group(1)
            current_parts = [m.group(2)]
            continue

        if current_key is None:
            # Stray content before any key (e.g. leading comments / blanks) —
            # ignore it silently.
            continue

        # Continuation line — preserve the line verbatim (including indentation
        # where it matters for PEM headers/footers, though we trim trailing
        # whitespace below).
        current_parts.append(line.rstrip())

    _commit()
    return result


def _normalize_private_key(raw: str) -> str:
    """Allow the user to paste the key with literal ``\\n`` or real newlines."""
    if not raw:
        return raw
    # dotenv returns the value as-is; collapse any "\n" escape sequences to
    # actual newlines so PyJWT can parse the PEM.
    if "\\n" in raw and "\n" not in raw:
        return raw.replace("\\n", "\n")
    return raw


def _as_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _as_int(value: str | int | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: str | float | None, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_database_url(raw: str) -> str:
    """Return an async SQLAlchemy URL for SQLite or PostgreSQL.

    Supported input forms:
      - ``sqlite:///./file.db`` -> ``sqlite+aiosqlite:///./file.db``
      - ``sqlite+aiosqlite:///./file.db`` -> unchanged
      - ``postgresql://...`` -> ``postgresql+asyncpg://...``
      - ``postgres://...`` -> ``postgresql+asyncpg://...``
      - ``postgresql+asyncpg://...`` -> unchanged
    """
    value = (raw or "").strip()
    if not value:
        return "sqlite+aiosqlite:///./github_api.db"
    if value.startswith("sqlite+aiosqlite://"):
        return value
    if value.startswith("sqlite://"):
        return value.replace("sqlite://", "sqlite+aiosqlite://", 1)
    if value.startswith("postgresql+asyncpg://"):
        return value
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+asyncpg://", 1)
    return value


def _normalize_state_backend(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in {"", "sql", "sqlite", "postgres", "postgresql"}:
        return "sql"
    if value in {"none", "stateless", "disabled", "off"}:
        return "none"
    return "sql"


def _warn_if_world_readable(path: Path) -> None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return
    if mode & 0o077:
        # Not fatal — just visible. We still want to run, but inform the user.
        # Using stderr keeps it out of any piped JSON output.
        import sys
        print(
            f"[warn] {path} is readable by other users (mode={oct(mode)}). "
            "Consider running: chmod 600 " + str(path),
            file=sys.stderr,
        )


def load_config(env_path: Path | None = None) -> Config:
    """Load and validate the GitHub App configuration.

    Resolution order:
      1. The explicit ``env_path`` argument (used by tests).
      2. A ``GITHUB_ENV_FILE`` environment variable (CI / containerized setups).
      3. The standard search list (CWD then project root) for ``.env`` /
         ``.ENV`` / ``.env.local``.
      4. Falls back to live OS environment variables (so ``docker run -e
         KEY=VALUE`` works without any .env file).
    """
    values: dict[str, str]

    if env_path is not None:
        values = _parse_env(env_path)
        path = env_path
    else:
        explicit = os.environ.get("GITHUB_ENV_FILE")
        if explicit:
            candidate = Path(explicit)
            if not candidate.is_file():
                raise FileNotFoundError(f"GITHUB_ENV_FILE points to a missing file: {candidate}")
            values = _parse_env(candidate)
            path = candidate
        else:
            found = _find_env_file_or_none()
            if found is not None:
                values = _parse_env(found)
                path = found
            else:
                # No .env file — fall back to environment variables.
                values = {k: os.environ[k] for k in REQUIRED_KEYS if k in os.environ}
                if not values:
                    cwd = Path.cwd()
                    project_root = Path(__file__).resolve().parent.parent
                    expected = ", ".join(
                        f"{b}/{n}" for b in (cwd, project_root) for n in ENV_CANDIDATES
                    )
                    raise FileNotFoundError(
                        "No .env file found and no GITHUB_* env vars set. "
                        f"Expected one of: {expected}. "
                        "Or set GITHUB_ENV_FILE=/path/to/file, or pass vars "
                        "directly in the environment."
                    )
                # No on-disk path to warn about; use a synthetic marker.
                path = Path("(environment)")

    def _value(name: str, default: str = "") -> str:
        raw = os.environ.get(name)
        if raw is not None:
            return raw
        raw = values.get(name)
        if raw is not None:
            return raw
        return default

    missing = [k for k in REQUIRED_KEYS if not _value(k)]
    if missing:
        raise EnvironmentError(
            f"Missing required keys: {', '.join(missing)}"
        )

    if str(path) != "(environment)":
        _warn_if_world_readable(path)

    return Config(
        app_id=str(_value("GITHUB_APP_ID")).strip(),
        private_key=_normalize_private_key(_value("GITHUB_PRIVATE_KEY")),
        installation_id=str(_value("GITHUB_INSTALLATION_ID")).strip(),
        organization=str(_value("GITHUB_ORGANIZATION")).strip(),
        env_path=path,
        api_auth_token=str(_value("API_AUTH_TOKEN")).strip(),
        state_backend=_normalize_state_backend(_value("STATE_BACKEND", "sql")),
        api_host=str(_value("API_HOST", "0.0.0.0")).strip() or "0.0.0.0",
        api_port=_as_int(_value("API_PORT"), 8000),
        api_workers=max(1, _as_int(_value("API_WORKERS"), 1)),
        github_api_base_url=str(_value("GITHUB_API_BASE_URL", "https://api.github.com")).strip() or "https://api.github.com",
        github_timeout_seconds=_as_float(_value("GITHUB_TIMEOUT_SECONDS"), 15.0),
        github_user_token=str(_value("GITHUB_USER_TOKEN")).strip(),
        database_url=_normalize_database_url(_value("DATABASE_URL", "sqlite:///./github_api.db")),
        db_echo=_as_bool(_value("DB_ECHO"), False),
        db_pool_size=max(1, _as_int(_value("DB_POOL_SIZE"), 20)),
        db_max_overflow=max(0, _as_int(_value("DB_MAX_OVERFLOW"), 40)),
        db_pool_timeout=max(1, _as_int(_value("DB_POOL_TIMEOUT"), 30)),
        db_pool_recycle=max(30, _as_int(_value("DB_POOL_RECYCLE"), 1800)),
        queue_maxsize=max(100, _as_int(_value("QUEUE_MAXSIZE"), 10000)),
        queue_workers=max(1, _as_int(_value("QUEUE_WORKERS"), 64)),
    )


def get_oauth_config() -> dict:
    """Return OAuth bits if present, used by the optional OAuth flow."""
    return {
        "client_id": os.environ.get("GITHUB_OAUTH_CLIENT_ID", ""),
        "client_secret": os.environ.get("GITHUB_OAUTH_CLIENT_SECRET", ""),
        "redirect_uri": os.environ.get("GITHUB_OAUTH_REDIRECT_URI", ""),
    }
