"""Run traces: structured event stream plus a readable renderer."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .contracts import RunResult

TraceEvent = dict[str, Any]
Tracer = Callable[[TraceEvent], None]

_SYMBOL = {
    "user_message": "you",
    "model_reply": "model",
    "tool_call": "call",
    "tool_result": "result",
    "validation_error": "invalid",
    "permission_denied": "denied",
    "tool_error": "error",
    "finish": "done",
}


def shorten(text: str, limit: int = 240) -> str:
    text = str(text).replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + " ..."


class ConsoleTracer:
    """Prints one compact line per event to ``write``."""

    def __init__(self, write: Callable[[str], None] | None = None, verbose: bool = True) -> None:
        self._write = write or print
        self._verbose = verbose
        self.events: list[TraceEvent] = []

    def __call__(self, event: TraceEvent) -> None:
        self.events.append(event)
        if not self._verbose:
            return
        self._write(render_event(event))


def render_event(event: TraceEvent) -> str:
    kind = event.get("type", "event")
    step = event.get("step")
    prefix = f"[{_SYMBOL.get(kind, kind)}]"
    if step is not None:
        prefix = f"[step {step}] {prefix}"

    if kind == "tool_call":
        rendered = ", ".join(f"{k}={v!r}" for k, v in event["arguments"].items())
        return f"{prefix} {event['name']}({shorten(rendered, 120)})"
    if kind in {"tool_result", "tool_error", "validation_error", "permission_denied"}:
        detail = event.get("content") or event.get("reason") or ""
        return f"{prefix} {event.get('name', '')} -> {shorten(detail)}"
    if kind == "finish":
        detail = event.get("output") or event.get("reason") or ""
        return f"{prefix} status={event.get('status')} steps={event.get('steps')} :: {shorten(detail)}"
    return f"{prefix} {shorten(event.get('content', ''))}"


def render_trace(result: RunResult) -> str:
    """Render a finished run from its message history."""
    lines: list[str] = []
    for message in result.messages:
        role = message.get("role")
        if role == "assistant":
            content = message.get("content", "")
            calls = message.get("tool_calls", [])
            if content:
                lines.append(f"assistant> {shorten(content)}")
            for call in calls:
                args = call["function"]["arguments"]
                lines.append(
                    f"assistant> call {call['function']['name']}({shorten(args, 120)})"
                )
        elif role == "tool":
            lines.append(f"tool[{message['name']}]> {shorten(message['content'])}")
        else:
            lines.append(f"{role}> {shorten(message.get('content', ''))}")
    lines.append(f"== status={result.status} steps={result.steps}")
    return "\n".join(lines)
