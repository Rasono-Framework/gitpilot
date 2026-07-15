"""Pydantic request/response schemas for the FastAPI service."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .api_models import OperationStatus, OperationType


class ApiModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class HealthResponse(ApiModel):
    status: str
    database: str
    state_backend: str
    queue_size: int
    queue_maxsize: int
    queue_workers: int


OwnerType = Literal["auto", "org", "user"]


class CreateRepoRequest(ApiModel):
    owner: Optional[str] = None
    owner_type: OwnerType = "auto"
    name: str = Field(min_length=1, max_length=100)
    private: bool = True
    description: str = Field(default="", max_length=500)
    auto_init: bool = True
    wait: bool = False


class CreateBranchRequest(ApiModel):
    branch: str = Field(min_length=1, max_length=255)
    from_branch: Optional[str] = Field(default=None, max_length=255)
    wait: bool = False


class PushFileRequest(ApiModel):
    branch: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=1024)
    content: str
    message: str = Field(min_length=1, max_length=500)
    update: bool = True
    wait: bool = False


class BatchFileItem(ApiModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str


class PushFilesRequest(ApiModel):
    branch: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1, max_length=500)
    files: list[BatchFileItem] = Field(min_length=1, max_length=100)
    wait: bool = False


class RepoSummary(ApiModel):
    owner: Optional[str] = None
    owner_type: Optional[str] = None
    name: str
    private: bool
    html_url: str


class OperationResponse(ApiModel):
    id: str
    op_type: OperationType
    status: OperationStatus
    owner: str
    repo: Optional[str] = None
    branch: Optional[str] = None
    path: Optional[str] = None
    request_id: str
    github_request_id: Optional[str] = None
    github_status: Optional[int] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    result: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime


class OperationAccepted(ApiModel):
    operation_id: str
    status: OperationStatus
    poll_url: str
