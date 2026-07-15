"""FastAPI application factory."""

from __future__ import annotations

import secrets
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Response, status

from .api_db import Database
from .api_models import OperationStatus, OperationType
from .api_schemas import (
    CreateBranchRequest,
    CreateRepoRequest,
    HealthResponse,
    OperationAccepted,
    OperationResponse,
    PushFileRequest,
    PushFilesRequest,
    RepoSummary,
)
from .api_service import GitHubOperationService, OperationQueueFull, OperationView, StatelessModeError
from .config import load_config
from .github_async import AsyncGitHubAppAuth, AsyncGitHubClient


def _operation_response(record: OperationView) -> OperationResponse:
    return OperationResponse(
        id=record.id,
        op_type=OperationType(record.op_type),
        status=OperationStatus(record.status),
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


def _accepted(record: OperationView) -> OperationAccepted:
    return OperationAccepted(
        operation_id=record.id,
        status=OperationStatus(record.status),
        poll_url=f"/v1/operations/{record.id}",
    )


async def _require_api_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = request.app.state.config.api_auth_token
    if not expected:
        raise HTTPException(status_code=500, detail="API_AUTH_TOKEN is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid Bearer token")


def _request_id(request: Request) -> str:
    return request.state.request_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    if not config.api_auth_token:
        raise RuntimeError(
            "API_AUTH_TOKEN is required in API mode. Refusing to start with an unauthenticated write API."
        )

    db = None
    session_factory = None
    if config.state_backend == "sql":
        db = Database(config)
        if not await db.has_schema():
            raise RuntimeError(
                "SQL schema is not initialized. Run 'python -m src.server init-db' before starting the API."
            )
        session_factory = db.session_factory

    limits = httpx.Limits(
        max_keepalive_connections=max(100, config.queue_workers * 2),
        max_connections=max(200, config.queue_workers * 4),
    )
    timeout = httpx.Timeout(config.github_timeout_seconds)
    http = httpx.AsyncClient(limits=limits, timeout=timeout, follow_redirects=False)
    auth = AsyncGitHubAppAuth(config, http)
    github = AsyncGitHubClient(
        auth,
        http,
        user_token=config.github_user_token,
        base_url=config.github_api_base_url,
    )
    service = GitHubOperationService(
        session_factory,
        github,
        default_owner=config.organization,
        queue_maxsize=config.queue_maxsize,
        worker_count=config.queue_workers,
        state_backend=config.state_backend,
    )
    await service.start()

    app.state.config = config
    app.state.db = db
    app.state.http = http
    app.state.github = github
    app.state.service = service
    try:
        yield
    finally:
        await service.stop()
        await http.aclose()
        if db is not None:
            await db.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="GitHub API Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        response: Response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    @app.get("/healthz", response_model=HealthResponse, tags=["system"])
    async def healthz(request: Request) -> HealthResponse:
        service: GitHubOperationService = request.app.state.service
        db = request.app.state.db
        return HealthResponse(
            status="ok",
            database="ok" if db is not None and await db.ping() else "disabled",
            state_backend=service.state_backend,
            queue_size=service.queue_size,
            queue_maxsize=service.queue_maxsize,
            queue_workers=service.worker_count,
        )

    @app.get("/readyz", response_model=HealthResponse, tags=["system"])
    async def readyz(request: Request) -> HealthResponse:
        return await healthz(request)

    secured = APIRouter(prefix="/v1", dependencies=[Depends(_require_api_token)])

    @secured.get("/owners/{owner}/repos", response_model=list[RepoSummary], tags=["repos"])
    async def list_repos(owner: str, request: Request, owner_type: str = "auto", limit: int = 30) -> list[RepoSummary]:
        github: AsyncGitHubClient = request.app.state.github
        repos = await github.list_repos(owner, owner_type=owner_type, per_page=min(max(limit, 1), 100))
        resolved_owner_type = owner_type if owner_type != "auto" else await github.resolve_owner_type(owner)
        return [
            RepoSummary(
                owner=owner,
                owner_type=resolved_owner_type,
                name=r["name"],
                private=r["private"],
                html_url=r["html_url"],
            )
            for r in repos
        ]

    @secured.get("/orgs/{org}/repos", response_model=list[RepoSummary], tags=["repos"])
    async def list_org_repos(org: str, request: Request, limit: int = 30) -> list[RepoSummary]:
        return await list_repos(org, request, owner_type="org", limit=limit)

    @secured.get("/users/{user}/repos", response_model=list[RepoSummary], tags=["repos"])
    async def list_user_repos(user: str, request: Request, limit: int = 30) -> list[RepoSummary]:
        return await list_repos(user, request, owner_type="user", limit=limit)

    @secured.get("/operations", response_model=list[OperationResponse], tags=["operations"])
    async def list_operations(request: Request, limit: int = 100) -> list[OperationResponse]:
        service: GitHubOperationService = request.app.state.service
        if not service.state_enabled:
            raise HTTPException(status_code=409, detail="operations are unavailable when STATE_BACKEND=none")
        records = await service.list_operations(limit=min(max(limit, 1), 500))
        return [_operation_response(record) for record in records]

    @secured.get("/operations/{operation_id}", response_model=OperationResponse, tags=["operations"])
    async def get_operation(operation_id: str, request: Request) -> OperationResponse:
        service: GitHubOperationService = request.app.state.service
        if not service.state_enabled:
            raise HTTPException(status_code=409, detail="operations are unavailable when STATE_BACKEND=none")
        record = await service.get_operation(operation_id)
        if record is None:
            raise HTTPException(status_code=404, detail="operation not found")
        return _operation_response(record)

    @secured.post("/repos", response_model=OperationAccepted | OperationResponse, status_code=status.HTTP_202_ACCEPTED, tags=["repos"])
    async def create_repo(body: CreateRepoRequest, request: Request):
        service: GitHubOperationService = request.app.state.service
        try:
            record = await service.submit(
                OperationType.create_repo,
                owner=body.owner,
                repo=body.name,
                branch=None,
                path=None,
                payload=body.model_dump(exclude={"wait"}),
                request_id=_request_id(request),
                wait=body.wait,
            )
        except OperationQueueFull as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (StatelessModeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if body.wait:
            return _operation_response(record)
        return _accepted(record)

    @secured.delete("/repos/{owner}/{repo}", response_model=OperationAccepted | OperationResponse, status_code=status.HTTP_202_ACCEPTED, tags=["repos"])
    async def delete_repo(owner: str, repo: str, request: Request, wait: bool = False):
        service: GitHubOperationService = request.app.state.service
        try:
            record = await service.submit(
                OperationType.delete_repo,
                owner=owner,
                repo=repo,
                branch=None,
                path=None,
                payload={"repo": repo},
                request_id=_request_id(request),
                wait=wait,
            )
        except OperationQueueFull as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (StatelessModeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if wait:
            return _operation_response(record)
        return _accepted(record)

    @secured.post("/repos/{owner}/{repo}/branches", response_model=OperationAccepted | OperationResponse, status_code=status.HTTP_202_ACCEPTED, tags=["branches"])
    async def create_branch(owner: str, repo: str, body: CreateBranchRequest, request: Request):
        service: GitHubOperationService = request.app.state.service
        try:
            record = await service.submit(
                OperationType.create_branch,
                owner=owner,
                repo=repo,
                branch=body.branch,
                path=None,
                payload={**body.model_dump(exclude={"wait"}), "repo": repo},
                request_id=_request_id(request),
                wait=body.wait,
            )
        except OperationQueueFull as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (StatelessModeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if body.wait:
            return _operation_response(record)
        return _accepted(record)

    @secured.post("/repos/{owner}/{repo}/files", response_model=OperationAccepted | OperationResponse, status_code=status.HTTP_202_ACCEPTED, tags=["files"])
    async def push_file(owner: str, repo: str, body: PushFileRequest, request: Request):
        service: GitHubOperationService = request.app.state.service
        try:
            record = await service.submit(
                OperationType.push_file,
                owner=owner,
                repo=repo,
                branch=body.branch,
                path=body.path,
                payload={**body.model_dump(exclude={"wait"}), "repo": repo},
                request_id=_request_id(request),
                wait=body.wait,
            )
        except OperationQueueFull as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (StatelessModeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if body.wait:
            return _operation_response(record)
        return _accepted(record)

    @secured.post("/repos/{owner}/{repo}/files/batch", response_model=OperationAccepted | OperationResponse, status_code=status.HTTP_202_ACCEPTED, tags=["files"])
    async def push_files(owner: str, repo: str, body: PushFilesRequest, request: Request):
        service: GitHubOperationService = request.app.state.service
        try:
            record = await service.submit(
                OperationType.push_files,
                owner=owner,
                repo=repo,
                branch=body.branch,
                path=None,
                payload={
                    "repo": repo,
                    "branch": body.branch,
                    "message": body.message,
                    "files": [item.model_dump() for item in body.files],
                },
                request_id=_request_id(request),
                wait=body.wait,
            )
        except OperationQueueFull as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (StatelessModeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if body.wait:
            return _operation_response(record)
        return _accepted(record)

    app.include_router(secured)
    return app
