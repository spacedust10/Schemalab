"""The demo script's safety checks."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_demo() -> ModuleType:
    spec = importlib.util.spec_from_file_location("demo", ROOT / "scripts" / "demo.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = load_demo()


@pytest.mark.parametrize(
    "url", ["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000", "https://x.io"]
)
def test_accepts_local_http_or_any_https(url: str) -> None:
    demo.check_api_url(url)


@pytest.mark.parametrize("url", ["http://schemalab.example.com", "ftp://localhost", "file:///etc"])
def test_rejects_cleartext_remote_or_odd_schemes(url: str) -> None:
    with pytest.raises(SystemExit):
        demo.check_api_url(url)


def test_only_registers_against_loopback() -> None:
    assert demo.is_loopback("http://localhost:8000")
    assert not demo.is_loopback("https://schemalab.example.com")


def test_clean_strips_terminal_control_sequences() -> None:
    assert demo.clean("ok\x1b[2J\x1b]0;pwned\x07 done\nnext") == "ok[2J]0;pwned done\nnext"


def test_password_is_generated_once_and_reused(tmp_path: Path) -> None:
    path = tmp_path / "demo_login.json"
    first = demo.demo_credentials(path)
    assert len(first["password"]) >= 24
    assert demo.demo_credentials(path) == first
    assert oct(path.stat().st_mode & 0o777) == "0o600"
