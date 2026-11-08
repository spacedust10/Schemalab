"""Checks on repo config files: Compose, CI and Dependabot."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(path: str) -> Any:
    return yaml.safe_load((ROOT / path).read_text())


def test_compose_ports_bind_to_localhost_only() -> None:
    # Published on 0.0.0.0, Postgres with its default password is open to the whole LAN.
    services = load("docker-compose.yml")["services"]
    published = [(name, p) for name, svc in services.items() for p in svc.get("ports", [])]
    assert published, "expected at least one published port"
    exposed = [(name, p) for name, p in published if not str(p).startswith("127.0.0.1:")]
    assert exposed == []
