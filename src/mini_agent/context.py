"""Keep the message history inside a character budget.

Compaction only shortens message bodies, it never drops a message: providers
reject a history whose ``tool`` messages are not paired with the ``tool_calls``
that requested them, so deleting entries would break the very next request.
Everything a model must keep verbatim (system prompt, the latest turns) is
protected, and every shortened body says so explicitly.
"""

from __future__ import annotations

import json
from collections.abc import MutableSequence
from typing import Any

_COMPACT_NOTE = "\n... [compacted {removed} chars]"


def estimate_chars(messages: list[dict[str, Any]]) -> int:
    """Rough context cost: message bodies plus serialised tool calls."""
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
        tool_calls = message.get("tool_calls")
        if tool_calls:
            total += len(json.dumps(tool_calls, ensure_ascii=False, default=str))
    return total


def compact_messages(
    messages: MutableSequence[dict[str, Any]],
    max_chars: int,
    keep_recent: int = 6,
    keep_head: int = 80,
) -> int:
    """Shrink old message bodies in place; return how many chars were freed.

    Best effort by design: system messages and the newest ``keep_recent`` turns
    are never touched, so a single huge tool result can still leave the history
    over budget. Pair it with ``max_tool_chars`` for a hard ceiling per result.
    """
    if max_chars <= 0:
        return 0

    total = estimate_chars(list(messages))
    if total <= max_chars:
        return 0

    count = len(messages)
    protected = {
        index
        for index, message in enumerate(messages)
        if message.get("role") == "system"
    }
    protected.update(range(max(0, count - keep_recent), count))

    removed = 0
    for index, message in enumerate(messages):
        if index in protected:
            continue
        content = message.get("content")
        if not isinstance(content, str) or len(content) <= keep_head:
            continue
        candidate = content[:keep_head] + _COMPACT_NOTE.format(
            removed=len(content) - keep_head
        )
        # Never "compact" a body into something longer than it already is.
        if len(candidate) >= len(content):
            continue
        message["content"] = candidate
        removed += len(content) - len(candidate)
        if total - removed <= max_chars:
            break

    return removed
