"""A gateway that answers from a script, through the real LiteLLM call path."""

from typing import Any

from schemalab.llm import Completion, Gateway

MODEL = "ollama_chat/llama3.1"


class ScriptedGateway(Gateway):
    """Replies in order; records every prompt so tests can check what the model saw."""

    def __init__(self, *replies: str | Exception) -> None:
        super().__init__([MODEL])
        self.replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> Completion:
        self.calls.append(messages)
        if not self.replies:
            raise AssertionError(f"unexpected model call #{len(self.calls)}")
        return super().complete(messages, mock_response=self.replies.pop(0), **kwargs)

    def prompt(self, i: int) -> str:
        return "\n".join(m["content"] for m in self.calls[i])
