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


@lru_cache
def get_settings() -> Settings:
    return Settings()
