"""The real dataset registry: provenance and safety of every source."""

from schemalab.datasets import REGISTRY


def test_registry_has_the_three_sample_datasets() -> None:
    assert {"nyc_taxi", "michigan_insurance", "olist"} <= REGISTRY.keys()


def test_registry_downloads_are_https_and_pinned() -> None:
    for ds in REGISTRY.values():
        for src in ds.sources:
            if src.url is not None:
                assert src.url.startswith("https://"), src.url
                assert src.sha256 is not None and len(src.sha256) == 64, f"{ds.name}.{src.table}"


def test_registry_table_names_are_unique() -> None:
    tables = [src.table for ds in REGISTRY.values() for src in ds.sources]
    assert len(tables) == len(set(tables))


def test_registry_records_license_and_homepage() -> None:
    for ds in REGISTRY.values():
        assert ds.license and ds.homepage, ds.name
