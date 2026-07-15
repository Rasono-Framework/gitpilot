"""Diagnostic that doesn't go through load_config().

Lets us check the real App state even when the .env is missing keys.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import _parse_env
import jwt
import requests
import time

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
values = _parse_env(ENV_PATH)
print("Loaded keys from .env:", sorted(values.keys()))

app_id = values.get("GITHUB_APP_ID", "").strip()
private_key = values["GITHUB_PRIVATE_KEY"]
if "\\n" in private_key and "\n" not in private_key:
    private_key = private_key.replace("\\n", "\n")

now = int(time.time())
payload = {"iat": now - 30, "exp": now + 540, "iss": app_id}
app_jwt = jwt.encode(payload, private_key, algorithm="RS256")

h = {
    "Authorization": f"Bearer {app_jwt}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

r = requests.get("https://api.github.com/app/installations", headers=h, timeout=10)
print(f"\nGET /app/installations -> {r.status_code}")
data = r.json()
print(f"count: {len(data)}")
for inst in data:
    acc = inst.get("account") or {}
    print(f"  id={inst.get('id')}  account={acc.get('login')}  type={acc.get('type')}  status={inst.get('status')}")
