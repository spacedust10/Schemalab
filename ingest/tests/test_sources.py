from datetime import UTC, datetime

import pytest

from ingest.sources import SourceError, quake_rows, sheet_export_url, usgs_url

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
