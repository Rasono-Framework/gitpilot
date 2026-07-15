"""Check installation status and pending permissions."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jwt
import requests
from src.config import _parse_env

v = _parse_env(Path(__file__).resolve().parent.parent / ".env")
app_id = v["GITHUB_APP_ID"]
key = v["GITHUB_PRIVATE_KEY"]
if "\\n" in key and "\n" not in key:
    key = key.replace("\\n", "\n")

payload = {"iat": int(time.time()) - 30, "exp": int(time.time()) + 540, "iss": app_id}
app_jwt = jwt.encode(payload, key, algorithm="RS256")
h = {
    "Authorization": f"Bearer {app_jwt}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# 1. List all installations
r = requests.get("https://api.github.com/app/installations", headers=h, timeout=10)
print(f"GET /app/installations -> {r.status_code}")
for inst in r.json():
    acc = inst.get("account") or {}
    print(f"  id={inst.get('id')}  account={acc.get('login')}  type={acc.get('type')}")
    print(f"    repository_selection: {inst.get('repository_selection')}")
    print(f"    permissions: {inst.get('permissions')}")
    print(f"    events: {inst.get('events')}")

# 2. Check suspended state
inst_id = v.get("GITHUB_INSTALLATION_ID", "").strip()
if inst_id:
    r = requests.get(f"https://api.github.com/app/installations/{inst_id}", headers=h, timeout=10)
    print(f"\nGET /app/installations/{inst_id} -> {r.status_code}")
    if r.ok:
        data = r.json()
        print(f"  suspended_at:     {data.get('suspended_at')}")
        print(f"  suspended_by:     {data.get('suspended_by')}")
        print(f"  contact_email:    {data.get('contact_email')}")
        print(f"  html_url:         {data.get('html_url')}")
