"""Diagnostic: what does the App JWT see right now?"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from src.auth import GitHubAppAuth
from src.config import load_config

auth = GitHubAppAuth(load_config())
app_jwt = auth._mint_app_jwt()
h = {
    "Authorization": f"Bearer {app_jwt}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

def check(label, method, url):
    r = requests.request(method, url, headers=h, timeout=10)
    if r.ok:
        body = r.json()
        if isinstance(body, list):
            print(f"{label:38s} -> {r.status_code} count={len(body)}")
        else:
            extra = ""
            if "slug" in body:
                extra = f" slug={body.get('slug')}"
            elif "login" in body and label.startswith("GET /users"):
                extra = f" login={body.get('login')}"
            print(f"{label:38s} -> {r.status_code}{extra}")
    else:
        msg = ""
        try:
            msg = r.json().get("message", "")[:60]
        except Exception:
            msg = r.text[:60]
        print(f"{label:38s} -> {r.status_code} {msg}")

check("GET /app",                          "GET",  "https://api.github.com/app")
check("GET /app/installations",            "GET",  "https://api.github.com/app/installations")
check("POST .../98765432/access_tokens",   "POST", "https://api.github.com/app/installations/98765432/access_tokens")
check("GET /users/hackville254",           "GET",  "https://api.github.com/users/hackville254")
check("GET /users/orvyx",                  "GET",  "https://api.github.com/users/orvyx")
