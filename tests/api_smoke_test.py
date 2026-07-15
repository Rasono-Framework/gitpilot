"""ASGI smoke test for the FastAPI app."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_api_smoke.db")
os.environ.setdefault("QUEUE_WORKERS", "2")
os.environ.setdefault("STATE_BACKEND", "sql")

from src.api_app import create_app  # noqa: E402
from src.api_service import GitHubOperationService  # noqa: E402


class FakeGitHub:
    async def get_authenticated_login(self) -> str:
        return "hackville254"

    async def resolve_owner_type(self, owner: str) -> str:
        return "org" if owner == "orvyx" else "user"

    async def list_repos(self, org: str, owner_type: str = "auto", per_page: int = 30):
        return [{"name": "demo", "private": True, "html_url": f"https://github.com/{org}/demo"}]

    async def create_repo(self, owner: str, name: str, **kwargs):
        actual_owner = owner or "hackville254"
        return {"name": name, "html_url": f"https://github.com/{actual_owner}/{name}"}

    async def delete_repo(self, owner: str, repo: str):
        return None

    async def create_branch(self, owner: str, repo: str, branch: str, **kwargs):
        return {"ref": f"refs/heads/{branch}"}

    async def push_file(self, owner: str, repo: str, path: str, content: str, **kwargs):
        return {"content": {"path": path}, "commit": {"sha": "abc123"}}

    async def push_files(self, owner: str, repo: str, files: list[dict[str, str]], **kwargs):
        return {"sha": "def456", "files": len(files)}


async def main() -> int:
    app = create_app()
    async with app.router.lifespan_context(app):
        app.state.github = FakeGitHub()
        service: GitHubOperationService = app.state.service
        service._github = app.state.github

        transport = httpx.ASGITransport(app=app)
        headers = {"Authorization": "Bearer test-api-token", "x-request-id": "smoke-api-1"}
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            r = await client.get("/healthz")
            assert r.status_code == 200, r.text

            r = await client.get("/v1/owners/orvyx/repos", headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()[0]["name"] == "demo", r.text

            r = await client.post(
                "/v1/repos",
                headers=headers,
                json={"name": "fastapi-smoke", "owner": "orvyx", "owner_type": "org", "wait": True},
            )
            assert r.status_code == 202, r.text
            assert r.json()["status"] == "succeeded", r.text

            r = await client.post(
                "/v1/repos",
                headers=headers,
                json={"name": "fastapi-smoke-user", "owner_type": "user", "wait": True},
            )
            assert r.status_code == 202, r.text
            assert r.json()["status"] == "succeeded", r.text

            r = await client.post(
                "/v1/repos/orvyx/fastapi-smoke/files",
                headers=headers,
                json={
                    "branch": "main",
                    "path": "README.md",
                    "content": "# smoke",
                    "message": "docs: smoke",
                    "wait": False,
                },
            )
            assert r.status_code == 202, r.text
            operation_id = r.json()["operation_id"]

            r = await client.get(f"/v1/operations/{operation_id}", headers=headers)
            assert r.status_code == 200, r.text

            r = await client.delete("/v1/repos/orvyx/fastapi-smoke?wait=true", headers=headers)
            assert r.status_code == 202, r.text
            assert r.json()["status"] == "succeeded", r.text

    db_path = PROJECT_ROOT / "test_api_smoke.db"
    if db_path.exists():
        db_path.unlink()
    wal = PROJECT_ROOT / "test_api_smoke.db-wal"
    shm = PROJECT_ROOT / "test_api_smoke.db-shm"
    if wal.exists():
        wal.unlink()
    if shm.exists():
        shm.unlink()
    print("API smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(__import__("asyncio").run(main()))
