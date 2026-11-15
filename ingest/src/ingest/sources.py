"""Where lake data comes from: uploaded files, the USGS earthquake feed, a public Google Sheet.

No source takes an arbitrary URL. The feed's address is fixed, and a Sheets link is parsed
down to its id and rebuilt, so a user cannot point this service at anything else.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"
USGS_PAGE = 2000  # events per run; the next run carries on from the newest one loaded
USGS_START = timedelta(days=7)  # how far back the first run looks

_SHEET_PATH = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})(/.*)?")
_GID = re.compile(r"gid=(\d+)")


class SourceError(ValueError):
    """A source refused its input or sent something this service cannot load."""


def sheet_export_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or parts.netloc != "docs.google.com":
        raise SourceError("Give a link to a Google Sheet (https://docs.google.com/spreadsheets/…).")
    match = _SHEET_PATH.fullmatch(parts.path)
    if not match:
        raise SourceError("That link is not a Google Sheet.")
    gid = _GID.search(parts.fragment) or _GID.search(parts.query)
    tab = gid.group(1) if gid else "0"
    return f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv&gid={tab}"


def usgs_url(since: datetime) -> str:
    query = {
        "format": "geojson",
        "orderby": "time-asc",
        "limit": USGS_PAGE,
        "minmagnitude": 2.5,
        "starttime": since.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return f"{USGS}?{urlencode(query)}"


def quake_rows(feed: dict[str, Any]) -> list[dict[str, Any]]:
    features = feed.get("features")
    if not isinstance(features, list):
        raise SourceError("The USGS feed sent no events.")
    rows = []
    for f in features:
        p, (lon, lat, depth) = f["properties"], f["geometry"]["coordinates"]
        rows.append(
            {
                "id": f["id"],
                "time": datetime.fromtimestamp(p["time"] / 1000, tz=UTC),
                "mag": p.get("mag"),
                "place": p.get("place"),
                "longitude": lon,
                "latitude": lat,
                "depth_km": depth,
            }
        )
    return rows
