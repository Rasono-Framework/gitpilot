"""Async database bootstrap for SQLite or PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .api_models import Base
from .config import Config


class Database:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.engine = self._build_engine(config)
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            autoflush=False,
            class_=AsyncSession,
        )

    def _build_engine(self, config: Config) -> AsyncEngine:
        url = config.database_url
        kwargs: dict = {
            "echo": config.db_echo,
            "pool_pre_ping": True,
        }
        if url.startswith("sqlite+aiosqlite://"):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_size"] = config.db_pool_size
            kwargs["max_overflow"] = config.db_max_overflow
            kwargs["pool_timeout"] = config.db_pool_timeout
            kwargs["pool_recycle"] = config.db_pool_recycle
        engine = create_async_engine(url, **kwargs)

        if url.startswith("sqlite+aiosqlite://"):
            @event.listens_for(engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=3000")
                cursor.close()

        return engine

    async def create_schema(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def ping(self) -> bool:
        try:
            async with self.session_factory() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False

    async def dispose(self) -> None:
        await self.engine.dispose()
