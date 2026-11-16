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


def test_lab_database_sits_on_its_own_internal_network() -> None:
    # The sandbox runs users' SQL: it must not share a network with the app database or reach
    # the internet. Only the API joins both (and the internal one to the lakehouse loader).
    compose = load("docker-compose.yml")
    services = compose["services"]
    assert compose["networks"]["lab"]["internal"] is True
    assert services["labdb"]["networks"] == ["lab"]
    assert "lab" not in services["postgres"]["networks"]
    assert "lab" not in services["web"]["networks"]
    assert set(services["api"]["networks"]) == {"app", "lab", "ingest"}
    assert "lab" not in services["ingest"]["networks"]


def test_api_image_ships_the_tpc_extensions_and_a_bench_volume() -> None:
    # The benchmark must not download DuckDB extensions at run time, and it needs disk.
    dockerfile = (ROOT / "Dockerfile.api").read_text()
    assert "INSTALL tpch" in dockerfile and "INSTALL tpcds" in dockerfile
    assert dockerfile.index("USER app") < dockerfile.index("INSTALL tpch")  # in the app's home
    assert "chown app /app/data/uploads /app/data/bench" in dockerfile
    compose = load("docker-compose.yml")
    assert "bench:/app/data/bench" in compose["services"]["api"]["volumes"]
    assert "bench" in compose["volumes"]


def test_api_image_has_a_writable_assignments_cache() -> None:
    # Assignment databases are rebuilt on demand, so the image's own disk is enough.
    dockerfile = (ROOT / "Dockerfile.api").read_text()
    assert "/app/data/assignments" in dockerfile.split("chown app", 1)[1].splitlines()[0]


def test_web_image_serves_the_public_folder() -> None:
    # The scratchpad's DuckDB-WASM files are copied into public/ at build time.
    dockerfile = (ROOT / "apps" / "web" / "Dockerfile").read_text()
    assert "COPY --from=build /app/public ./public" in dockerfile


LAKE_SECRETS = (
    "GARAGE_RPC_SECRET",
    "LAKE_S3_KEY",
    "LAKE_S3_SECRET",
    "LAKE_DB_PASSWORD",
    "INGEST_TOKEN",
)


def test_lake_secrets_have_no_committed_defaults() -> None:
    # `${X:?...}`: Compose stops with a message unless .env sets it (scripts/lake-env.sh does).
    text = (ROOT / "docker-compose.yml").read_text()
    for name in LAKE_SECRETS:
        assert f"${{{name}:-" not in text, name
        assert f"${{{name}:?" in text, name


def test_the_lake_is_reachable_only_through_ingest() -> None:
    compose = load("docker-compose.yml")
    services, networks = compose["services"], compose["networks"]
    assert networks["lake"] == {"internal": True}
    assert networks["ingest"] == {"internal": True}
    for name in ("garage", "lakedb", "ingest"):
        assert "ports" not in services[name], name
    assert services["garage"]["networks"] == ["lake"]
    assert services["lakedb"]["networks"] == ["lake"]
    assert set(services["ingest"]["networks"]) == {"lake", "ingest", "outbound"}
    assert "ingest" in services["api"]["networks"]
    assert "lake" not in services["api"]["networks"]
    assert services["api"]["environment"]["SCHEMALAB_INGEST_URL"] == "http://ingest:8100"


def test_the_quick_start_makes_the_lake_secrets_compose_requires() -> None:
    readme = (ROOT / "README.md").read_text()
    start = readme[readme.index("## Quick start") :]
    assert start.index("lake-env.sh") < start.index("docker compose up")
    assert (ROOT / "scripts" / "lake-env.sh").stat().st_mode & 0o111


def test_ingest_reaches_the_internet_only_through_the_allow_list_proxy() -> None:
    # Fixed hosts in code are the first wall; the proxy is the second, for a bug or a
    # compromised dependency in ingest. Only the proxy sits on the routable network.
    compose = load("docker-compose.yml")
    services, networks = compose["services"], compose["networks"]
    assert networks["outbound"] == {"internal": True}
    assert "egress" not in services["ingest"]["networks"]
    assert set(services["proxy"]["networks"]) == {"outbound", "egress"}
    assert [n for n, s in services.items() if "egress" in s.get("networks", [])] == ["proxy"]
    assert re.fullmatch(r"ubuntu/squid:[\d.]+-[\d.]+_\w+", services["proxy"]["image"])
    assert "ports" not in services["proxy"]
    env = services["ingest"]["environment"]
    assert env["HTTPS_PROXY"] == "http://proxy:3128"
    assert set(env["NO_PROXY"].split(",")) == {"garage", "lakedb"}


def test_the_proxy_allows_only_the_source_hosts_over_connect_443() -> None:
    hosts = (ROOT / "ingest" / "proxy" / "allowed-hosts.txt").read_text().split()
    assert hosts == ["earthquake.usgs.gov", "docs.google.com", ".googleusercontent.com"]
    rules = [
        line.split()
        for line in (ROOT / "ingest" / "proxy" / "squid.conf").read_text().splitlines()
        if line.startswith("http_access")
    ]
    # Plain HTTP and other ports are refused before the host list is consulted.
    assert rules == [
        ["http_access", "deny", "!CONNECT"],
        ["http_access", "deny", "!SSL_ports"],
        ["http_access", "allow", "allowed"],
        ["http_access", "deny", "all"],
    ]
