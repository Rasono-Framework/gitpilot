"""Smoke test for stateless API mode."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")
os.environ.setdefault("STATE_BACKEND", "none")
os.environ.setdefault("GITHUB_ORGANIZATION", "")
os.environ.setdefault("GITHUB_APP_ID", "test-app-id")
os.environ.setdefault("GITHUB_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nTEST\n-----END RSA PRIVATE KEY-----")
os.environ.setdefault("GITHUB_INSTALLATION_ID", "123456")

from src.api_app import create_app  # noqa: E402
from src.api_service import GitHubOperationService  # noqa: E402


class FakeGitHub:
    async def get_authenticated_login(self) -> str:
        return "hackville254"

    async def create_repo(self, owner, name: str, **kwargs):
        actual_owner = owner or "hackville254"
        return {"name": name, "html_url": f"https://github.com/{actual_owner}/{name}"}


async def main() -> int:
    app = create_app()
    async with app.router.lifespan_context(app):
        app.state.github = FakeGitHub()
        service: GitHubOperationService = app.state.service
        service._github = app.state.github

        transport = httpx.ASGITransport(app=app)
        headers = {"Authorization": "Bearer test-api-token"}
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            r = await client.get("/healthz")
            assert r.status_code == 200, r.text
            assert r.json()["state_backend"] == "none", r.text

            r = await client.post(
                "/v1/repos",
                headers=headers,
                json={"name": "stateless-demo", "owner_type": "user", "wait": True},
            )
            assert r.status_code == 202, r.text
            assert r.json()["status"] == "succeeded", r.text

            r = await client.post(
                "/v1/repos",
                headers=headers,
                json={"name": "stateless-demo-async", "owner_type": "user", "wait": False},
            )
            assert r.status_code == 409, r.text

            r = await client.get("/v1/operations", headers=headers)
            assert r.status_code == 409, r.text

    print("Stateless API smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(__import__("asyncio").run(main()))
