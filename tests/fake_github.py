"""Tiny in-process GitHub API stand-in.

Speaks just enough of the REST API to exercise ``create_repo``,
``create_branch`` and ``push_file``. Returns plausible JSON, simulates a
default branch called ``main``, and keeps an in-memory tree of commits
so successive operations on the same ``owner/repo`` behave consistently.

This is *not* a generic mock — it's a focused harness for the smoke test.
The point is to prove the CLI's request shapes are correct end-to-end,
not to model every GitHub edge case.

Run via the ``live_demo.py`` script.
"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# In-memory state.
_state: dict[str, Any] = {
    "repos": {},        # "owner/name" -> {default_branch, files:{path:sha}}
    "branches": {},     # "owner/name" -> {branch: sha}
    "commits": {},      # sha -> {"tree": {"sha": tree_sha}}
    "trees": {},        # tree_sha -> [items]
    "blobs": {},        # blob_sha -> decoded content
}


def _new_sha() -> str:
    return secrets.token_hex(20)


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeGitHub/0.1"

    # -------- helpers --------
    def _json(self, status: int, body: Any) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("x-ratelimit-remaining", "5000")
        self.send_header("x-ratelimit-limit", "5000")
        self.send_header("x-github-request-id", "fake-" + secrets.token_hex(6))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _route(self, method: str, path: str, body: dict) -> None:
        # ---- installations / app ----
        if method == "GET" and path == "/app/installations":
            # Pretend the App has one installation pointing at this server.
            return self._json(200, [{
                "id": 99999999,
                "account": {"login": "orvyx", "type": "Organization"},
                "status": "active",
            }])

        if method == "POST" and "/access_tokens" in path:
            return self._json(201, {
                "token": "ghs_fake_installation",
                "expires_at": "2030-01-01T00:00:00Z",
            })

        # ---- orgs/{org}/repos  (create) ----
        if method == "POST" and path.startswith("/orgs/") and path.endswith("/repos"):
            org = path.split("/")[2]
            name = body["name"]
            key = f"{org}/{name}"
            _state["repos"][key] = {"default_branch": "main", "files": {}}
            _state["branches"].setdefault(key, {})["main"] = _new_sha()
            return self._json(201, {
                "name": name,
                "full_name": key,
                "private": body.get("private", True),
                "html_url": f"https://github.com/{key}",
                "default_branch": "main",
            })

        # ---- /repos/{owner}/{repo}  (read) ----
        if method == "GET" and path.startswith("/repos/") and path.count("/") == 3:
            owner, repo = path.split("/")[2], path.split("/")[3]
            key = f"{owner}/{repo}"
            if key not in _state["repos"]:
                return self._json(404, {"message": "Not Found"})
            r = _state["repos"][key]
            return self._json(200, {
                "name": repo, "full_name": key,
                "default_branch": r["default_branch"],
            })

        # ---- /repos/{owner}/{repo}/branches/{branch}  (get sha) ----
        if method == "GET" and "/branches/" in path:
            parts = path.split("/")
            owner, repo, branch = parts[2], parts[3], parts[5]
            key = f"{owner}/{repo}"
            sha = _state["branches"].get(key, {}).get(branch)
            if not sha:
                return self._json(404, {"message": "Not Found"})
            return self._json(200, {"name": branch, "commit": {"sha": sha}})

        # ---- /repos/{owner}/{repo}/git/refs  (create branch) ----
        if method == "POST" and path.endswith("/git/refs"):
            parts = path.split("/")
            owner, repo = parts[2], parts[3]
            key = f"{owner}/{repo}"
            ref = body["ref"]  # refs/heads/feat/x
            branch = ref.split("/")[-1]
            _state["branches"].setdefault(key, {})[branch] = body["sha"]
            return self._json(201, {"ref": ref, "object": {"sha": body["sha"]}})

        # ---- /repos/{owner}/{repo}/git/commits/{sha}  (get tree) ----
        if method == "GET" and "/git/commits/" in path:
            sha = path.split("/")[-1]
            c = _state["commits"].get(sha, {"tree": {"sha": _new_sha()}})
            _state["commits"].setdefault(sha, c)
            return self._json(200, c)

        # ---- /repos/{owner}/{repo}/git/blobs  (create blob) ----
        if method == "POST" and path.endswith("/git/blobs"):
            sha = _new_sha()
            _state["blobs"][sha] = body.get("content", "")
            return self._json(201, {"sha": sha})

        # ---- /repos/{owner}/{repo}/git/trees  (create tree) ----
        if method == "POST" and path.endswith("/git/trees"):
            sha = _new_sha()
            _state["trees"][sha] = body.get("tree", [])
            return self._json(201, {"sha": sha})

        # ---- /repos/{owner}/{repo}/git/commits  (create commit) ----
        if method == "POST" and path.endswith("/git/commits"):
            sha = _new_sha()
            _state["commits"][sha] = {"tree": {"sha": body["tree"]}}
            return self._json(201, {"sha": sha})

        # ---- /repos/{owner}/{repo}/git/refs/heads/{branch}  (update) ----
        if method == "PATCH" and "/git/refs/heads/" in path:
            parts = path.split("/")
            owner, repo, branch = parts[2], parts[3], parts[-1]
            key = f"{owner}/{repo}"
            _state["branches"].setdefault(key, {})[branch] = body["sha"]
            return self._json(200, {"object": {"sha": body["sha"]}})

        # ---- /repos/{owner}/{repo}/contents/{path}  (get) ----
        if method == "GET" and "/contents/" in path:
            parts = path.split("/")
            owner, repo = parts[2], parts[3]
            key = f"{owner}/{repo}"
            file_path = "/".join(parts[5:])
            ref = self.path.split("ref=")[-1] if "ref=" in self.path else "main"
            branches = _state["branches"].get(key, {})
            sha = branches.get(ref)
            if not sha:
                return self._json(404, {"message": "Not Found"})
            r = _state["repos"].get(key, {})
            file_sha = r.get("files", {}).get(file_path)
            if not file_sha:
                return self._json(404, {"message": "Not Found"})
            return self._json(200, {"sha": file_sha, "path": file_path})

        # ---- /repos/{owner}/{repo}/contents/{path}  (put) ----
        if method == "PUT" and "/contents/" in path:
            parts = path.split("/")
            owner, repo = parts[2], parts[3]
            key = f"{owner}/{repo}"
            file_path = "/".join(parts[5:])
            _state["repos"].setdefault(key, {"files": {}})["files"][file_path] = _new_sha()
            return self._json(201, {
                "commit": {"sha": _new_sha()},
                "content": {"path": file_path},
            })

        # ---- default: not implemented ----
        self._json(404, {"message": f"unmocked: {method} {path}"})

    # -------- HTTP verbs --------
    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import urlparse
        parsed = urlparse(self.path)
        self._route("GET", parsed.path, {})

    def do_POST(self) -> None:  # noqa: N802
        from urllib.parse import urlparse
        parsed = urlparse(self.path)
        self._route("POST", parsed.path, self._read_body())

    def do_PUT(self) -> None:  # noqa: N802
        from urllib.parse import urlparse
        parsed = urlparse(self.path)
        self._route("PUT", parsed.path, self._read_body())

    def do_PATCH(self) -> None:  # noqa: N802
        from urllib.parse import urlparse
        parsed = urlparse(self.path)
        self._route("PATCH", parsed.path, self._read_body())

    def do_DELETE(self) -> None:  # noqa: N802
        from urllib.parse import urlparse
        parsed = urlparse(self.path)
        self._route("DELETE", parsed.path, {})

    def log_message(self, fmt, *args):  # noqa: ANN001
        # Mirror the request to stderr so the demo shows what's happening.
        import sys
        sys.stderr.write("  fake_github  " + (fmt % args) + "\n")


def start(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Start the fake server on a free port. Return (server, base_url)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    actual_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{actual_port}"
