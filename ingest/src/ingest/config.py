"""Settings, all from the environment, so no secret sits in the image or a file."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class S3:
    endpoint: str  # host:port of an S3-compatible store
    key: str
    secret: str
    region: str = "garage"


@dataclass(frozen=True)
class Settings:
    catalog: str  # postgresql://user:password@host:port/db, the DuckLake catalog
    data_root: str  # s3://bucket, or a local directory in tests
    publish_dir: Path  # where silver and gold are copied for Ask
    work_dir: Path  # dlt's pipeline state
    inbox_dir: Path = Path("/data/inbox")  # files the API hands over, deleted once loaded
    token: str = ""  # the API sends it on every call; only the API holds it
    s3: S3 | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        s3 = None
        if env.get("INGEST_S3_ENDPOINT"):
            s3 = S3(env["INGEST_S3_ENDPOINT"], env["INGEST_S3_KEY"], env["INGEST_S3_SECRET"])
        return cls(
            catalog=env["INGEST_CATALOG"],
            data_root=env["INGEST_DATA_ROOT"],
            publish_dir=Path(env["INGEST_PUBLISH_DIR"]),
            work_dir=Path(env.get("INGEST_WORK_DIR", "/tmp/ingest")),  # noqa: S108
            inbox_dir=Path(env["INGEST_INBOX_DIR"]),
            token=env.get("INGEST_TOKEN", ""),
            s3=s3,
        )
