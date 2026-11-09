"""Model gateway: one call that tries free models in order until one answers.

Local Ollama models come first. Free cloud tiers (Groq, Google AI Studio) are fallbacks and are
used only when their key is set, so the default setup needs no keys at all.
"""

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import litellm
import openai

from schemalab.config import Settings

# Provider prefix -> env var that must be set to use it. Ollama needs none.
_KEYS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}


class AllModelsFailedError(RuntimeError):
    """Every model in the list raised an error."""


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    failed: list[str] = field(default_factory=list)


def available_models(models: Sequence[str]) -> list[str]:
    """Drop cloud models whose API key is not set."""
    return [m for m in models if (key := _KEYS.get(m.split("/", 1)[0])) is None or os.getenv(key)]


class Gateway:
    def __init__(
        self, models: Sequence[str], options: dict[str, dict[str, Any]] | None = None
    ) -> None:
        """`options` holds extra LiteLLM arguments per model, such as api_base."""
        if not models:
            raise ValueError("the gateway needs at least one model")
        self.models = list(models)
        self.options = options or {}

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> Completion:
        errors: list[str] = []
        failed: list[str] = []
        for model in self.models:
            start = time.perf_counter()
            try:
                resp = litellm.completion(
                    model=model, messages=messages, **self.options.get(model, {}), **kwargs
                )
            # LiteLLM maps every provider failure (rate limit, timeout, auth, connection) to an
            # OpenAIError subclass. Anything else is a bug here and should surface, not fall back.
            except openai.OpenAIError as e:
                failed.append(model)
                errors.append(f"{model}: {type(e).__name__}: {e}")
                continue
            usage = resp.usage
            return Completion(
                text=resp.choices[0].message.content or "",
                model=model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                latency_ms=(time.perf_counter() - start) * 1000,
                failed=failed,
            )
        raise AllModelsFailedError("all models failed:\n" + "\n".join(errors))


class NoModelsError(RuntimeError):
    """None of the configured models can be used."""


def gateway_from_settings(settings: Settings) -> Gateway:
    models = available_models(settings.models)
    if not models:
        missing = sorted({k for m in settings.models if (k := _KEYS.get(m.split("/", 1)[0]))})
        hint = f"set {' or '.join(missing)}, or " if missing else ""
        raise NoModelsError(
            f"No usable model in {settings.models}: {hint}add an ollama_chat/... model."
        )
    # Temperature 0: SQL should not change run to run, and sampling made llama3.1 invent columns.
    options: dict[str, dict[str, Any]] = {m: {"temperature": 0} for m in models}
    for m in models:
        if m.startswith("ollama"):
            options[m]["api_base"] = settings.ollama_api_base
    return Gateway(models, options)
