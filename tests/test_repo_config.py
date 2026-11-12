"""Checks on repo config files: Compose, CI and Dependabot."""

import re
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


def test_compose_keeps_postgres_off_the_host() -> None:
    # Only the API needs Postgres, over the Compose network. Publishing 5432
    # clashes with a local Postgres and stops `docker compose up` from starting.
    postgres = load("docker-compose.yml")["services"]["postgres"]
    assert "ports" not in postgres


def test_dependabot_watches_every_ecosystem() -> None:
    # Nothing flagged the vulnerable Next.js on Day 1; every lockfile needs a watcher.
    updates = load(".github/dependabot.yml")["updates"]
    watched = {(u["package-ecosystem"], u["directory"]) for u in updates}
    assert watched >= {
        ("uv", "/"),
        ("npm", "/apps/web"),
        ("github-actions", "/"),
        ("docker", "/"),
        ("docker", "/apps/web"),
    }


def test_ci_actions_are_pinned_to_commit_shas() -> None:
    # A tag like @v4 can be moved to new code; a 40-char SHA cannot.
    uses = [
        step["uses"]
        for path in (ROOT / ".github/workflows").glob("*.yml")
        for job in load(str(path.relative_to(ROOT)))["jobs"].values()
        for step in job["steps"]
        if "uses" in step
    ]
    assert uses, "expected at least one action"
    unpinned = [u for u in uses if not re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u)]
    assert unpinned == []


def test_api_container_sees_sample_data_read_only() -> None:
    # /ask reads data/samples/schemalab.duckdb; the container must not be able to change it.
    volumes = load("docker-compose.yml")["services"]["api"].get("volumes", [])
    assert "./data/samples:/app/data/samples:ro" in volumes


def test_api_uses_postgres_through_psycopg3() -> None:
    # A bare postgresql:// URL makes SQLAlchemy load psycopg2, which is not installed.
    env = load("docker-compose.yml")["services"]["api"]["environment"]
    assert env["SCHEMALAB_DATABASE_URL"].startswith("postgresql+psycopg://")


def test_ci_runs_the_web_unit_tests() -> None:
    steps = load(".github/workflows/ci.yml")["jobs"]["web"]["steps"]
    assert {"run": "npm test"} in steps


def test_api_keeps_uploads_in_a_named_volume_it_owns() -> None:
    # Uploads must survive a rebuild, and the non-root app user must be able to write them.
    compose = load("docker-compose.yml")
    assert "uploads:/app/data/uploads" in compose["services"]["api"]["volumes"]
    assert "uploads" in compose["volumes"]
    dockerfile = (ROOT / "Dockerfile.api").read_text()
    assert "chown app /app/data/uploads" in dockerfile
    assert dockerfile.index("chown app /app/data/uploads") < dockerfile.index("USER app")


def test_ci_runs_the_lab_tests_against_postgres() -> None:
    # The lab tests skip without LAB_TEST_DSN; CI must provide it so they never silently skip.
    job = load(".github/workflows/ci.yml")["jobs"]["python"]
    assert job["services"]["postgres"]["image"].startswith("postgres:17")
    assert job["env"]["LAB_TEST_DSN"].startswith("postgresql://")


def test_lab_database_is_reachable_only_by_the_api() -> None:
    # The lab runs users' SQL; its database must not be published on the host.
    compose = load("docker-compose.yml")
    lab = compose["services"]["labdb"]
    assert "ports" not in lab and lab["image"].startswith("postgres:17")
    env = compose["services"]["api"]["environment"]
    assert env["SCHEMALAB_LAB_ADMIN_DSN"].startswith("postgresql://")
    assert "@labdb:5432/" in env["SCHEMALAB_LAB_ADMIN_DSN"]
