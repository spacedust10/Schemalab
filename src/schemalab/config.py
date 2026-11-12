"""Runtime settings, read from the environment or a .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCHEMALAB_", env_file=".env", extra="ignore")

    env: str = "dev"
    cors_origins: list[str] = ["http://localhost:3000"]

    # Tried in order. Cloud entries are skipped unless their free-tier key is set.
    models: list[str] = [
        "ollama_chat/llama3.1",
        "groq/openai/gpt-oss-120b",
        "gemini/gemini-2.5-flash",
    ]
    ollama_api_base: str = "http://localhost:11434"
    db_path: str = "data/samples/schemalab.duckdb"
    # One DuckDB file per user for uploaded .csv and .xlsx files.
    uploads_dir: str = "data/uploads"
    # Off: the model sees only the schema and its own SQL. On: it also sees up to 20 result
    # rows, to word the answer. Raw table rows are never sent either way.
    share_results_with_model: bool = False
    # The sample data is public, so the model may see its category values ('Under 65 years').
    # Uploaded tables share them only when the user asks, per question.
    share_sample_categories: bool = True
    # Accounts and history. Compose points this at Postgres.
    database_url: str = "sqlite:///data/app.db"
    # Transaction lab: a superuser DSN for a separate, disposable Postgres. Empty turns it off.
    lab_admin_dsn: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
