"""Ask the running API a question and print each agent step as it streams in.

    uv run python scripts/demo.py "Which pickup borough had the most taxi trips?"

Signs in as a local demo account. On first run against a local API it creates the account
with a random password, kept in data/demo_login.json (git-ignored). Set API to use another
address; anything that is not local must use https, and no account is created there.
"""

import json
import os
import secrets
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API = os.environ.get("API", f"http://localhost:{os.environ.get('API_PORT', '8000')}")
CREDENTIALS = Path("data/demo_login.json")
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def is_loopback(url: str) -> bool:
    return urllib.parse.urlsplit(url).hostname in LOOPBACK


def check_api_url(url: str) -> None:
    """Allow http only on this machine; anything remote must be https."""
    scheme = urllib.parse.urlsplit(url).scheme
    if scheme == "https" or (scheme == "http" and is_loopback(url)):
        return
    sys.exit(f"refusing {url!r}: use https, or http only on localhost")


def clean(text: Any) -> str:
    """Drop control characters so streamed text cannot drive the terminal."""
    return "".join(ch for ch in str(text) if ch == "\n" or ch.isprintable())


def demo_credentials(path: Path = CREDENTIALS) -> dict[str, str]:
    if path.exists():
        return dict(json.loads(path.read_text()))
    creds = {"email": "demo@schemalab.local", "password": secrets.token_urlsafe(24)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600)
    path.write_text(json.dumps(creds))
    return creds


def post(path: str, body: dict[str, Any], token: str | None = None) -> Any:
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    url = urllib.parse.urljoin(API, path)
    req = urllib.request.Request(url, json.dumps(body).encode(), headers)  # noqa: S310 - checked
    return urllib.request.urlopen(req, timeout=300)  # noqa: S310 - scheme checked above


def sign_in() -> str:
    creds = demo_credentials()
    try:
        with post("/auth/login", creds) as resp:
            return str(json.load(resp)["token"])
    except urllib.error.HTTPError as e:
        # Only a plain "wrong credentials" means the account may not exist yet. A 429 or a
        # server error is reported as it is, not retried as a registration.
        if e.code != 401:
            sys.exit(f"sign-in failed: {e.code} {clean(e.read().decode())}")
    if not is_loopback(API):
        sys.exit(f"no demo account on {API}; this script only creates one on localhost")
    try:
        with post("/auth/register", creds) as resp:
            return str(json.load(resp)["token"])
    except urllib.error.HTTPError as e:
        sys.exit(f"could not create the demo account: {e.code} {clean(e.read().decode())}")


def show(event: dict[str, Any], elapsed: float) -> None:
    step = event["step"]
    head = f"[{elapsed:5.1f}s] {clean(step).upper():7}"
    pad = " " * 17
    if step == "plan":
        print(head, textwrap.indent(clean(event["text"]), pad).lstrip())
    elif step == "sql":
        print(head, f"attempt {event['attempt']}: {clean(event['sql'])}")
    elif step == "result":
        print(head, f"{len(event['rows'])} rows, columns {clean(event['columns'])}")
        for row in event["rows"][:8]:
            print(pad, clean(row))
    elif step == "critic":
        verdict = {True: "agrees", False: f"objects: {event.get('issue')}", None: "no verdict"}
        print(head, clean(verdict[event["ok"]]))
    elif step == "retry":
        print(
            head, f"{event['stage']} rejected attempt {event['attempt']}: {clean(event['message'])}"
        )
    elif step == "answer":
        conf, prov = event["confidence"], event["provenance"]
        print(head, clean(event["text"]))
        print(pad, clean(f"confidence {conf['label']}: {'; '.join(conf['reasons'])}"))
        print(pad, clean(f"tables {prov['tables']}, models {prov['models']}"))
    elif step == "error":
        print(head, clean(f"{event['stage']}: {event['message']}"))
    else:
        print(head, clean({k: v for k, v in event.items() if k != "step"}))


def main() -> None:
    check_api_url(API)
    question = " ".join(sys.argv[1:]) or "Which pickup borough had the most taxi trips?"
    token = sign_in()
    print(f"Q: {question}\n")
    start = time.monotonic()
    with post("/ask", {"question": question}, token) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if line.startswith("data: "):
                show(json.loads(line[6:]), time.monotonic() - start)


if __name__ == "__main__":
    main()
