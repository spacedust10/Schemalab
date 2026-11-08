"""Sample datasets: fetch, verify and load into one DuckDB file.

This is trusted setup code, so it uses a normal writable connection. Model-written SQL never
runs here; it goes through `guards.run.run_query` against the built file.

    python -m schemalab.datasets                 # build every dataset that can be fetched
    python -m schemalab.datasets nyc_taxi        # build named datasets only
"""

import hashlib
import re
import shutil
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

DEFAULT_DB = Path("data/samples/schemalab.duckdb")
DEFAULT_CACHE = Path("data/cache")

_TABLE_NAME = re.compile(r"[a-z_][a-z0-9_]*")
_READERS = {".csv": "read_csv_auto", ".parquet": "read_parquet"}


class ChecksumError(ValueError):
    """A downloaded file does not match its pinned SHA-256."""


class MissingFileError(FileNotFoundError):
    """A source that cannot be downloaded automatically has not been put in place."""


@dataclass(frozen=True)
class Source:
    table: str
    url: str | None = None
    filename: str = ""
    sha256: str | None = None

    def __post_init__(self) -> None:
        if not _TABLE_NAME.fullmatch(self.table):
            raise ValueError(f"unsafe table name: {self.table!r}")
        if not self.filename:
            if self.url is None:
                raise ValueError(f"{self.table}: a source without a url needs a filename")
            name = Path(urllib.parse.urlparse(self.url).path).name
            object.__setattr__(self, "filename", name)
        if Path(self.filename).suffix not in _READERS:
            raise ValueError(f"{self.table}: unsupported file type {self.filename!r}")


@dataclass(frozen=True)
class Dataset:
    name: str
    sources: list[Source]
    manual_steps: str = ""
    description: str = ""
    license: str = ""
    homepage: str = ""
    tags: list[str] = field(default_factory=list)


def build_database(db_path: Path, datasets: list[Dataset], cache_dir: Path) -> None:
    """Fetch and verify every file first, then load them, so a bad file loads nothing."""
    files = [(src, _fetch(ds, src, cache_dir)) for ds in datasets for src in ds.sources]

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    with duckdb.connect(str(tmp)) as con:
        for src, path in files:
            # Identifiers cannot be bound as parameters. The table name passed the regex in
            # Source and the reader comes from a fixed dict; the path is bound.
            sql = f"CREATE TABLE {src.table} AS SELECT * FROM {_READERS[path.suffix]}(?)"  # noqa: S608
            con.execute(sql, [str(path)])
    tmp.replace(db_path)


def _fetch(ds: Dataset, src: Source, cache_dir: Path) -> Path:
    dest = cache_dir / ds.name / src.filename
    if not dest.exists():
        if src.url is None:
            raise MissingFileError(
                f"{ds.name}: put {src.filename} in {dest.parent}/. {ds.manual_steps}".strip()
            )
        _download(src.url, dest)

    if src.sha256 is not None:
        actual = hashlib.sha256(dest.read_bytes()).hexdigest()
        if actual != src.sha256:
            dest.unlink()
            raise ChecksumError(f"{ds.name}.{src.table}: expected {src.sha256}, got {actual}")
    return dest


def _download(url: str, dest: Path) -> None:
    if urllib.parse.urlparse(url).scheme not in {"https", "file"}:
        raise ValueError(f"refusing to fetch non-https url: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, part.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(resp, out)
    part.replace(dest)


_TLC = "https://d37ci6vzurychx.cloudfront.net"
_OLIST_FILES = {
    "olist_customers": "olist_customers_dataset.csv",
    "olist_geolocation": "olist_geolocation_dataset.csv",
    "olist_order_items": "olist_order_items_dataset.csv",
    "olist_order_payments": "olist_order_payments_dataset.csv",
    "olist_order_reviews": "olist_order_reviews_dataset.csv",
    "olist_orders": "olist_orders_dataset.csv",
    "olist_products": "olist_products_dataset.csv",
    "olist_sellers": "olist_sellers_dataset.csv",
    "olist_category_translation": "product_category_name_translation.csv",
}

REGISTRY: dict[str, Dataset] = {
    ds.name: ds
    for ds in [
        Dataset(
            name="nyc_taxi",
            description="NYC yellow taxi trips for January 2024 (about 3 million rows) and the "
            "taxi zone lookup. The scale dataset.",
            license="NYC Open Data terms of use",
            homepage="https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page",
            tags=["scale", "time", "geo"],
            sources=[
                Source(
                    table="taxi_trips",
                    url=f"{_TLC}/trip-data/yellow_tripdata_2024-01.parquet",
                    sha256="c4d59da7bbc8abaeeeb1727947ee93d9891a71acb42854bd80db1571b2030510",
                ),
                Source(
                    table="taxi_zones",
                    url=f"{_TLC}/misc/taxi_zone_lookup.csv",
                    sha256="1a99e105092230f8620f301edcca7f80d3080642ff404d28ed957d3fa222c8ed",
                ),
            ],
        ),
        Dataset(
            name="michigan_insurance",
            description="Small Area Health Insurance Estimates for Michigan counties by year, "
            "age, race, sex and income, in long format.",
            license="Public domain (US Census Bureau SAHIE, via data.michigan.gov)",
            homepage="https://data.michigan.gov/d/gc3p-9fhp",
            tags=["michigan", "public-health", "long-format"],
            sources=[
                Source(
                    table="mi_insurance",
                    url="https://data.michigan.gov/api/views/gc3p-9fhp/rows.csv",
                    filename="sahie_insurance.csv",
                    # data.michigan.gov serves the live table; if the state republishes it, this
                    # check fails and the pin must be updated after a look at the new file.
                    sha256="f8bc1951fd586564e99f0b3262bc6e07049c90ff7977dc1dc6fb6b2a7eeaa737",
                ),
            ],
        ),
        Dataset(
            name="olist",
            description="Brazilian e-commerce orders, items, payments, reviews, customers, "
            "sellers and products from Olist, 2016 to 2018. The business-metrics dataset.",
            license="CC BY-NC-SA 4.0",
            homepage="https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce",
            tags=["business", "joins", "time"],
            manual_steps="Kaggle needs a login: download the archive from "
            "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce and unzip the CSVs.",
            sources=[Source(table=t, filename=f) for t, f in _OLIST_FILES.items()],
        ),
    ]
}


def main(argv: list[str]) -> None:
    names = argv or [n for n, ds in REGISTRY.items() if all(s.url for s in ds.sources)]
    unknown = sorted(set(names) - REGISTRY.keys())
    if unknown:
        sys.exit(f"unknown dataset(s): {', '.join(unknown)}. Known: {', '.join(REGISTRY)}")
    build_database(DEFAULT_DB, [REGISTRY[n] for n in names], DEFAULT_CACHE)
    print(f"built {DEFAULT_DB} with: {', '.join(names)}")


if __name__ == "__main__":
    main(sys.argv[1:])
