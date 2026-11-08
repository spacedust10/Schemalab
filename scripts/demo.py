"""Ask the running API a question and print each agent step as it streams in.

    uv run python scripts/demo.py "Which pickup borough had the most taxi trips?"

Signs in as a local demo account (created on first run). Set API to use another address.
"""

import json
import os
import sys
import textwrap
import time
import urllib.error
import urllib.request
from typing import Any

API = os.environ.get("API", f"http://localhost:{os.environ.get('API_PORT', '8000')}")
DEMO_EMAIL = "demo@schemalab.local"
DEMO_PASSWORD = "schemalab local demo"  # noqa: S105 - local-only demo account, not a secret


def post(path: str, body: dict[str, Any], token: str | None = None) -> Any:
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, json.dumps(body).encode(), headers)  # noqa: S310
    return urllib.request.urlopen(req, timeout=300)  # noqa: S310


def sign_in() -> str:
    creds = {"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
    for path in ("/auth/login", "/auth/register"):
        try:
            with post(path, creds) as resp:
                return str(json.load(resp)["token"])
        except urllib.error.HTTPError as e:
            if path == "/auth/register":
                sys.exit(f"could not sign in: {e.code} {e.read().decode()}")
    raise AssertionError("unreachable")


def show(event: dict[str, Any], elapsed: float) -> None:
    step = event["step"]
    head = f"[{elapsed:5.1f}s] {step.upper():7}"
    if step == "plan":
        print(head, textwrap.indent(event["text"], " " * 17).lstrip())
    elif step == "sql":
        print(head, f"attempt {event['attempt']}: {event['sql']}")
    elif step == "result":
        print(head, f"{len(event['rows'])} rows, columns {event['columns']}")
        for row in event["rows"][:8]:
            print(" " * 17, row)
    elif step == "critic":
        verdict = {True: "agrees", False: f"objects: {event.get('issue')}", None: "no verdict"}
        print(head, verdict[event["ok"]])
    elif step == "retry":
        print(head, f"{event['stage']} rejected attempt {event['attempt']}: {event['message']}")
    elif step == "answer":
        conf = event["confidence"]
        print(head, event["text"])
        print(" " * 17, f"confidence {conf['label']}: {'; '.join(conf['reasons'])}")
        prov = event["provenance"]
        print(" " * 17, f"tables {prov['tables']}, models {prov['models']}")
    elif step == "error":
        print(head, f"{event['stage']}: {event['message']}")
    else:
        print(head, {k: v for k, v in event.items() if k != "step"})


def main() -> None:
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
