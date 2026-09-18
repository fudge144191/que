"""Concrete ModelClient implementations.

The Agent only depends on :class:`~mini_agent.contracts.ModelClient`, so these
live beside the CLI: one real (OpenAI-compatible) client over the standard
library, and one replay helper so the CLI can run offline.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .contracts import ModelReply, ToolCall


def _to_provider_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert internal tool specs into narrow OpenAI-style tool definitions."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec.get("description", ""),
                "parameters": spec.get("input_schema", {"type": "object"}),
            },
        }
        for spec in tools
    ]


class OpenAICompatibleModel:
    """Chat-completions client for any OpenAI-compatible endpoint."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        api_key_env: str = "OPENAI_API_KEY",
        timeout: float = 60.0,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key = api_key or os.environ.get(api_key_env, "")
        if not self.api_key:
            raise ValueError(
                f"missing API key: pass --api-key or set ${api_key_env}"
            )

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelReply:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = _to_provider_tools(tools)
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens

        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"request failed: {exc}") from exc

        try:
            message = raw["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"unexpected response shape: {raw}") from exc

        tool_calls = [
            ToolCall(
                id=call["id"],
                name=call["function"]["name"],
                arguments=json.loads(call["function"].get("arguments") or "{}"),
            )
            for call in message.get("tool_calls", [])
        ]
        return ModelReply(content=message.get("content") or "", tool_calls=tool_calls)


def load_replies(path: str | Path) -> list[ModelReply]:
    """Load scripted replies from JSON, e.g. for offline CLI demos.

    Shape: ``[{"content": "...", "tool_calls": [{"id", "name", "arguments"}]}]``
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    replies: list[ModelReply] = []
    for item in data:
        calls = [
            ToolCall(
                id=call["id"],
                name=call["name"],
                arguments=call.get("arguments", {}),
            )
            for call in item.get("tool_calls", [])
        ]
        replies.append(ModelReply(content=item.get("content", ""), tool_calls=calls))
    return replies
