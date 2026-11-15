from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from ingest import sources
from ingest.sources import (
    SourceError,
    download,
    quake_rows,
    sheet_export_url,
    sheet_host,
    usgs_host,
    usgs_url,
)

SHEET = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"


def test_a_sheet_link_becomes_its_csv_export() -> None:
    url = f"https://docs.google.com/spreadsheets/d/{SHEET}/edit#gid=42"
    assert sheet_export_url(url) == (
        f"https://docs.google.com/spreadsheets/d/{SHEET}/export?format=csv&gid=42"
    )


def test_a_sheet_link_without_a_tab_exports_the_first_tab() -> None:
    url = f"https://docs.google.com/spreadsheets/d/{SHEET}/edit?usp=sharing"
    assert sheet_export_url(url).endswith("export?format=csv&gid=0")


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.google.com/spreadsheets/d/abc/edit",  # not https
        "https://docs.google.com.evil.example/spreadsheets/d/abc/edit",
        "https://evil.example/?u=https://docs.google.com/spreadsheets/d/abc",
        "https://user@docs.google.com/spreadsheets/d/abc/edit",
        "https://docs.google.com:8443/spreadsheets/d/abc/edit",
        "https://docs.google.com/document/d/abc/edit",
        "https://docs.google.com/spreadsheets/d/../../x/edit",
        "file:///etc/passwd",
    ],
)
def test_anything_but_a_google_sheets_link_is_refused(url: str) -> None:
    with pytest.raises(SourceError):
        sheet_export_url(url)


def test_the_usgs_url_is_fixed_and_starts_after_the_cursor() -> None:
    since = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    url = usgs_url(since)
    assert url.startswith("https://earthquake.usgs.gov/fdsnws/event/1/query?")
    assert "starttime=2026-11-01T12%3A00%3A00" in url
    assert "orderby=time-asc" in url


def test_quakes_flatten_to_one_row_each() -> None:
    feed = {
        "features": [
            {
                "id": "us7000abcd",
                "properties": {"time": 1762000000000, "mag": 4.6, "place": "10 km N of Somewhere"},
                "geometry": {"coordinates": [-120.5, 35.25, 8.1]},
            }
        ]
    }
    assert quake_rows(feed) == [
        {
            "id": "us7000abcd",
            "time": datetime.fromtimestamp(1762000000, UTC),
            "mag": 4.6,
            "place": "10 km N of Somewhere",
            "longitude": -120.5,
            "latitude": 35.25,
            "depth_km": 8.1,
        }
    ]


def test_a_feed_without_features_is_refused() -> None:
    with pytest.raises(SourceError):
        quake_rows({"type": "error"})


class Reply:
    def __init__(self, status: int, body: bytes = b"", location: str = "") -> None:
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self.is_redirect = 300 <= status < 400 and bool(location)
        self._body = body

    def iter_content(self, size: int) -> list[bytes]:
        return [self._body[i : i + size] for i in range(0, len(self._body), size)]

    def close(self) -> None:
        pass


def fake_get(*replies: Reply) -> tuple[list[str], Callable[..., Reply]]:
    asked: list[str] = []

    def get(url: str, **kwargs: Any) -> Reply:
        assert kwargs["allow_redirects"] is False
        assert kwargs["timeout"]
        asked.append(url)
        return replies[len(asked) - 1]

    return asked, get


EXPORT = f"https://docs.google.com/spreadsheets/d/{SHEET}/export?format=csv&gid=0"


def test_a_sheet_follows_google_to_its_own_content_host() -> None:
    cdn = "https://doc-0s-1c-sheets.googleusercontent.com/export/abc"
    asked, get = fake_get(Reply(307, location=cdn), Reply(200, b"a,b\n1,2\n"))
    assert download(EXPORT, sheet_host, get) == b"a,b\n1,2\n"
    assert asked == [EXPORT, cdn]


@pytest.mark.parametrize(
    "location",
    [
        "http://169.254.169.254/latest/meta-data/",
        "https://evil.example/x",
        "https://googleusercontent.com.evil.example/x",
        "http://doc-0s.googleusercontent.com/x",
    ],
)
def test_a_sheet_redirect_anywhere_else_is_refused(location: str) -> None:
    asked, get = fake_get(Reply(302, location=location))
    with pytest.raises(SourceError):
        download(EXPORT, sheet_host, get)
    assert asked == [EXPORT]


def test_a_private_sheet_says_how_to_share_it() -> None:
    _, get = fake_get(Reply(302, location="https://accounts.google.com/ServiceLogin?x=1"))
    with pytest.raises(SourceError, match="Anyone with the link"):
        download(EXPORT, sheet_host, get)


def test_a_download_stops_at_the_size_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "MAX_BYTES", 10)
    _, get = fake_get(Reply(200, b"x" * 11))
    with pytest.raises(SourceError, match="larger"):
        download(EXPORT, sheet_host, get)


def test_a_failed_download_is_reported() -> None:
    _, get = fake_get(Reply(404))
    with pytest.raises(SourceError, match="404"):
        download(EXPORT, sheet_host, get)


def test_redirect_loops_end() -> None:
    _, get = fake_get(*[Reply(302, location=EXPORT)] * 10)
    with pytest.raises(SourceError, match="redirect"):
        download(EXPORT, sheet_host, get)


def test_the_usgs_feed_may_not_redirect_at_all() -> None:
    url = usgs_url(datetime(2026, 11, 1, tzinfo=UTC))
    _, get = fake_get(Reply(301, location="https://earthquake.usgs.gov/elsewhere"))
    with pytest.raises(SourceError):
        download(url, usgs_host, get)
    assert not usgs_host("https://earthquake.usgs.gov.evil.example/fdsnws/event/1/query?x")
