from __future__ import annotations

from typing import Any

from mini_agent import (
    Agent,
    AllowAll,
    FakeModel,
    ModelReply,
    Tool,
    ToolCall,
    compact_messages,
    estimate_chars,
)


def make_echo() -> Tool:
    return Tool(
        name="echo",
        description="Return the supplied text.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        handler=lambda text: text,
    )


def sample_messages() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "You are Mini Agent."},
        {"role": "user", "content": "U" * 400},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"text":"..."}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "echo", "content": "T" * 400},
        {"role": "assistant", "content": "Final answer."},
    ]


def test_nothing_happens_under_the_budget() -> None:
    messages = sample_messages()
    before = [message["content"] for message in messages]

    assert compact_messages(messages, max_chars=100_000) == 0
    assert [message["content"] for message in messages] == before


def test_compaction_keeps_every_message_and_protects_system_and_recent() -> None:
    messages = sample_messages()
    initial = estimate_chars(messages)

    removed = compact_messages(messages, max_chars=300, keep_recent=1)

    assert removed > 0
    assert estimate_chars(messages) == initial - removed
    # Messages are never dropped: dropping a tool message would break the
    # tool_calls / tool_call_id pairing the provider expects.
    assert len(messages) == 5
    assert messages[0] == {"role": "system", "content": "You are Mini Agent."}
    assert messages[3]["tool_call_id"] == "c1"
    assert messages[4]["content"] == "Final answer."
    assert "[compacted" in messages[1]["content"]
    assert "[compacted" in messages[3]["content"]
    assert estimate_chars(messages) < initial


def test_compaction_does_not_grow_short_bodies() -> None:
    messages = [{"role": "user", "content": "x" * 90} for _ in range(4)]

    removed = compact_messages(messages, max_chars=10, keep_recent=1)

    # 90 chars -> 80 + a 26-char note would be longer, so it is left alone.
    assert removed == 0
    assert all(message["content"] == "x" * 90 for message in messages)


def test_agent_compacts_before_the_next_model_call() -> None:
    long_text = "x" * 400
    model = FakeModel(
        [
            ModelReply(tool_calls=[ToolCall("c1", "echo", {"text": long_text})]),
            ModelReply(content="Done."),
        ]
    )
    events: list[dict[str, Any]] = []
    agent = Agent(
        model,
        [make_echo()],
        permission_policy=AllowAll(),
        max_steps=3,
        tracer=events.append,
        max_context_chars=100,
        compact_keep_recent=1,
    )

    result = agent.run("q" * 200)

    assert result.status == "completed"
    assert result.output == "Done."
    assert "context_compacted" in [event["type"] for event in events]
    # The compacted history is what the model actually received: the old user
    # message shrank, while the newest turn stayed verbatim.
    sent = model.requests[-1]["messages"]
    assert "[compacted" in sent[0]["content"]
    assert sent[-1]["content"] == "x" * 400
