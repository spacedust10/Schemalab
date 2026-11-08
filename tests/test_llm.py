"""Model gateway. LiteLLM's mock_response runs the real call path with no network."""

import litellm
import pytest

from schemalab.config import Settings
from schemalab.llm import AllModelsFailedError, Gateway, available_models, gateway_from_settings

MSG = [{"role": "user", "content": "How many orders?"}]


def rate_limited() -> Exception:
    return litellm.RateLimitError("slow down", llm_provider="groq", model="m")


def test_returns_text_model_tokens_and_latency() -> None:
    gw = Gateway(
        ["ollama_chat/llama3.1"], options={"ollama_chat/llama3.1": {"mock_response": "SELECT 1"}}
    )
    out = gw.complete(MSG)
    assert out.text == "SELECT 1"
    assert out.model == "ollama_chat/llama3.1"
    assert out.prompt_tokens > 0 and out.completion_tokens > 0
    assert out.latency_ms >= 0
    assert out.failed == []


def test_falls_back_to_the_next_model_on_error() -> None:
    gw = Gateway(
        ["ollama_chat/llama3.1", "groq/openai/gpt-oss-120b"],
        options={
            "ollama_chat/llama3.1": {"mock_response": rate_limited()},
            "groq/openai/gpt-oss-120b": {"mock_response": "SELECT 2"},
        },
    )
    out = gw.complete(MSG)
    assert out.text == "SELECT 2"
    assert out.model == "groq/openai/gpt-oss-120b"
    assert out.failed == ["ollama_chat/llama3.1"]


def test_raises_with_every_error_when_all_models_fail() -> None:
    gw = Gateway(
        ["ollama_chat/one", "ollama_chat/two"],
        options={
            m: {"mock_response": rate_limited()} for m in ["ollama_chat/one", "ollama_chat/two"]
        },
    )
    with pytest.raises(AllModelsFailedError) as err:
        gw.complete(MSG)
    assert "ollama_chat/one" in str(err.value) and "ollama_chat/two" in str(err.value)


def test_needs_at_least_one_model() -> None:
    with pytest.raises(ValueError, match="model"):
        Gateway([])


def test_skips_cloud_models_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    models = ["ollama_chat/llama3.1", "groq/openai/gpt-oss-120b", "gemini/gemini-2.5-flash"]
    assert available_models(models) == ["ollama_chat/llama3.1"]


def test_keeps_cloud_models_that_have_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    models = ["ollama_chat/llama3.1", "groq/openai/gpt-oss-120b"]
    assert available_models(models) == models


def test_a_bug_is_raised_not_hidden_by_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # LiteLLM's mock_response can only raise its own provider errors, so stand in for the call.
    def broken(**_: object) -> None:
        raise KeyError("bug in our code")

    monkeypatch.setattr(litellm, "completion", broken)
    with pytest.raises(KeyError):
        Gateway(["ollama_chat/one", "ollama_chat/two"]).complete(MSG)


def test_gateway_from_settings_uses_configured_models_and_ollama_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCHEMALAB_MODELS", '["ollama_chat/llama3.1", "groq/openai/gpt-oss-120b"]')
    monkeypatch.setenv("SCHEMALAB_OLLAMA_API_BASE", "http://host.docker.internal:11434")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    gw = gateway_from_settings(Settings())
    assert gw.models == ["ollama_chat/llama3.1"]
    assert gw.options["ollama_chat/llama3.1"]["api_base"] == "http://host.docker.internal:11434"
