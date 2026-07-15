"""Operation queue + optional persistence for the FastAPI service."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .api_models import OperationRecord, OperationStatus, OperationType
from .client import GitHubApiError
from .github_async import AsyncGitHubClient


@dataclass
class OperationView:
    id: str
    op_type: str
    status: str
    owner: str
    repo: Optional[str]
    branch: Optional[str]
    path: Optional[str]
    request_id: str
    github_request_id: Optional[str]
    github_status: Optional[int]
    duration_ms: Optional[int]
    error_message: Optional[str]
    payload: Optional[dict[str, Any]]
    result: Optional[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


@dataclass
class QueuedOperation:
    operation_id: str
    op_type: OperationType
    payload: dict[str, Any]


class OperationQueueFull(RuntimeError):
    pass


class StatelessModeError(RuntimeError):
    pass


def _to_view(record: OperationRecord) -> OperationView:
    return OperationView(
        id=record.id,
        op_type=record.op_type,
        status=record.status,
        owner=record.owner,
        repo=record.repo,
        branch=record.branch,
        path=record.path,
        request_id=record.request_id,
        github_request_id=record.github_request_id,
        github_status=record.github_status,
        duration_ms=record.duration_ms,
        error_message=record.error_message,
        payload=record.payload,
        result=record.result,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class GitHubOperationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None,
        github: AsyncGitHubClient,
        *,
        default_owner: str,
        queue_maxsize: int,
        worker_count: int,
        state_backend: str = "sql",
    ) -> None:
        self._session_factory = session_factory
        self._github = github
        self._default_owner = default_owner
        self._state_backend = state_backend
        self._queue: asyncio.Queue[QueuedOperation] = asyncio.Queue(maxsize=queue_maxsize)
        self._worker_count = worker_count if self.state_enabled else 0
        self._workers: list[asyncio.Task] = []

    @property
    def state_enabled(self) -> bool:
        return self._state_backend == "sql" and self._session_factory is not None

    @property
    def state_backend(self) -> str:
        return self._state_backend

    @property
    def queue_size(self) -> int:
        return self._queue.qsize() if self.state_enabled else 0

    @property
    def queue_maxsize(self) -> int:
        return self._queue.maxsize if self.state_enabled else 0

    @property
    def worker_count(self) -> int:
        return self._worker_count

    async def start(self) -> None:
        if not self.state_enabled or self._workers:
            return
        for idx in range(self._worker_count):
            self._workers.append(asyncio.create_task(self._worker_loop(idx), name=f"github-worker-{idx}"))

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._workers.clear()

    async def submit(
        self,
        op_type: OperationType,
        *,
        owner: Optional[str],
        repo: Optional[str],
        branch: Optional[str],
        path: Optional[str],
        payload: dict[str, Any],
        request_id: str,
        wait: bool,
    ) -> OperationView:
        resolved_owner = await self._resolve_owner(op_type, owner, payload)

        if not self.state_enabled:
            if not wait:
                raise StatelessModeError("STATE_BACKEND=none requires wait=true because operations are not persisted")
            operation = self._new_view(
                operation_id=str(uuid.uuid4()),
                op_type=op_type,
                owner=resolved_owner,
                repo=repo,
                branch=branch,
                path=path,
                request_id=request_id,
                payload={**payload, "owner": resolved_owner},
            )
            return await self._execute_inline(operation, op_type, {**payload, "owner": resolved_owner})

        record = await self._create_record(
            op_type=op_type,
            owner=resolved_owner,
            repo=repo,
            branch=branch,
            path=path,
            payload=payload,
            request_id=request_id,
        )
        job = QueuedOperation(
            operation_id=record.id,
            op_type=op_type,
            payload={**payload, "owner": resolved_owner},
        )
        if wait:
            await self._execute(job)
        else:
            try:
                self._queue.put_nowait(job)
            except asyncio.QueueFull as exc:
                await self._mark_queue_full(record.id)
                raise OperationQueueFull("operation queue is full") from exc

        fresh = await self.get_operation(record.id)
        if fresh is None:
            raise RuntimeError(f"operation {record.id} disappeared")
        return fresh

    async def get_operation(self, operation_id: str) -> Optional[OperationView]:
        if not self.state_enabled:
            return None
        assert self._session_factory is not None
        async with self._session_factory() as session:
            record = await session.get(OperationRecord, operation_id)
            return _to_view(record) if record is not None else None

    async def list_operations(self, limit: int = 100) -> list[OperationView]:
        if not self.state_enabled:
            return []
        assert self._session_factory is not None
        async with self._session_factory() as session:
            result = await session.scalars(
                select(OperationRecord).order_by(desc(OperationRecord.created_at)).limit(limit)
            )
            return [_to_view(record) for record in result]

    async def _resolve_owner(self, op_type: OperationType, owner: Optional[str], payload: dict[str, Any]) -> str:
        if owner:
            return owner
        if op_type is OperationType.create_repo:
            owner_type = payload.get("owner_type", "auto")
            if owner_type in {"user", "auto"} and not self._default_owner:
                return await self._github.get_authenticated_login()
        if self._default_owner:
            return self._default_owner
        raise ValueError("owner is required when no default GITHUB_ORGANIZATION is configured")

    def _new_view(
        self,
        *,
        operation_id: str,
        op_type: OperationType,
        owner: str,
        repo: Optional[str],
        branch: Optional[str],
        path: Optional[str],
        request_id: str,
        payload: dict[str, Any],
    ) -> OperationView:
        now = datetime.now(timezone.utc)
        return OperationView(
            id=operation_id,
            op_type=op_type.value,
            status=OperationStatus.queued.value,
            owner=owner,
            repo=repo,
            branch=branch,
            path=path,
            request_id=request_id,
            github_request_id=None,
            github_status=None,
            duration_ms=None,
            error_message=None,
            payload=payload,
            result=None,
            created_at=now,
            updated_at=now,
        )

    async def _create_record(
        self,
        *,
        op_type: OperationType,
        owner: str,
        repo: Optional[str],
        branch: Optional[str],
        path: Optional[str],
        payload: dict[str, Any],
        request_id: str,
    ) -> OperationView:
        assert self._session_factory is not None
        record = OperationRecord(
            id=str(uuid.uuid4()),
            op_type=op_type.value,
            status=OperationStatus.queued.value,
            owner=owner,
            repo=repo,
            branch=branch,
            path=path,
            request_id=request_id,
            payload=payload,
            result=None,
            github_request_id=None,
            github_status=None,
            duration_ms=None,
            error_message=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
        return _to_view(record)

    async def _mark_queue_full(self, operation_id: str) -> None:
        assert self._session_factory is not None
        async with self._session_factory() as session:
            record = await session.get(OperationRecord, operation_id)
            if record is None:
                return
            record.status = OperationStatus.failed.value
            record.error_message = "operation queue is full"
            record.updated_at = datetime.now(timezone.utc)
            await session.commit()

    async def _worker_loop(self, _worker_index: int) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._execute(job)
            finally:
                self._queue.task_done()

    async def _execute_inline(
        self,
        operation: OperationView,
        op_type: OperationType,
        payload: dict[str, Any],
    ) -> OperationView:
        started = time.perf_counter()
        operation.status = OperationStatus.running.value
        operation.updated_at = datetime.now(timezone.utc)
        try:
            operation.result = await self._dispatch(op_type, payload)
            operation.status = OperationStatus.succeeded.value
            operation.github_status = 200
            operation.error_message = None
        except GitHubApiError as exc:
            operation.status = OperationStatus.failed.value
            operation.github_status = exc.status
            operation.github_request_id = exc.request_id
            operation.error_message = exc.message
            operation.result = None
        except Exception as exc:  # noqa: BLE001
            operation.status = OperationStatus.failed.value
            operation.error_message = str(exc)
            operation.result = None
        operation.duration_ms = int((time.perf_counter() - started) * 1000)
        operation.updated_at = datetime.now(timezone.utc)
        return operation

    async def _execute(self, job: QueuedOperation) -> None:
        started = time.perf_counter()
        assert self._session_factory is not None
        async with self._session_factory() as session:
            record = await session.get(OperationRecord, job.operation_id)
            if record is None:
                return
            record.status = OperationStatus.running.value
            record.updated_at = datetime.now(timezone.utc)
            await session.commit()

        try:
            result = await self._dispatch(job.op_type, job.payload)
            github_status = 200
            github_request_id = None
            error_message = None
            final_status = OperationStatus.succeeded.value
        except GitHubApiError as exc:
            result = None
            github_status = exc.status
            github_request_id = exc.request_id
            error_message = exc.message
            final_status = OperationStatus.failed.value
        except Exception as exc:  # noqa: BLE001
            result = None
            github_status = None
            github_request_id = None
            error_message = str(exc)
            final_status = OperationStatus.failed.value

        duration_ms = int((time.perf_counter() - started) * 1000)
        async with self._session_factory() as session:
            record = await session.get(OperationRecord, job.operation_id)
            if record is None:
                return
            record.status = final_status
            record.result = result
            record.github_status = github_status
            record.github_request_id = github_request_id
            record.error_message = error_message
            record.duration_ms = duration_ms
            record.updated_at = datetime.now(timezone.utc)
            await session.commit()

    async def _dispatch(self, op_type: OperationType, payload: dict[str, Any]) -> dict[str, Any]:
        owner = payload["owner"]
        if op_type is OperationType.create_repo:
            return await self._github.create_repo(
                owner if payload.get("owner_type", "auto") != "user" else payload.get("owner"),
                payload["name"],
                owner_type=payload.get("owner_type", "auto"),
                private=payload.get("private", True),
                description=payload.get("description", ""),
                auto_init=payload.get("auto_init", True),
            )
        if op_type is OperationType.delete_repo:
            await self._github.delete_repo(owner, payload["repo"])
            return {"deleted": True, "owner": owner, "repo": payload["repo"]}
        if op_type is OperationType.create_branch:
            return await self._github.create_branch(
                owner,
                payload["repo"],
                payload["branch"],
                from_branch=payload.get("from_branch"),
            )
        if op_type is OperationType.push_file:
            return await self._github.push_file(
                owner,
                payload["repo"],
                payload["path"],
                payload["content"],
                message=payload["message"],
                branch=payload["branch"],
                update=payload.get("update", True),
            )
        if op_type is OperationType.push_files:
            return await self._github.push_files(
                owner,
                payload["repo"],
                payload["files"],
                message=payload["message"],
                branch=payload["branch"],
            )
        raise ValueError(f"Unsupported operation type: {op_type}")
