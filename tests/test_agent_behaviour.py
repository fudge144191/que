from __future__ import annotations

from typing import Any

from mini_agent import (
    Agent,
    AllowAll,
    CallbackPolicy,
    ConsequentialPolicy,
    DenyAll,
    FakeModel,
    ModelReply,
    PermissionDecision,
    Tool,
    ToolCall,
    WhitelistPolicy,
)


def make_echo(consequential: bool = False) -> Tool:
    return Tool(
        name="echo",
        description="Return the supplied text.",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "extra": {"type": "integer", "minimum": 1},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        handler=lambda text, extra=1: text * extra,
        consequential=consequential,
    )


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def test_invalid_arguments_are_reported_to_the_model() -> None:
    model = FakeModel(
        [
            ModelReply(tool_calls=[ToolCall("c1", "echo", {"extra": -3})]),
            ModelReply(content="Recovered."),
        ]
    )
    agent = Agent(model, [make_echo()], permission_policy=AllowAll(), max_steps=3)

    result = agent.run("Break it.")

    tool_message = result.messages[-2]
    assert tool_message["role"] == "tool"
    assert "invalid arguments" in tool_message["content"]
    assert "required" in tool_message["content"]
    assert result.status == "completed"
    assert result.output == "Recovered."


def test_permission_denial_is_reported_to_the_model() -> None:
    model = FakeModel(
        [
            ModelReply(tool_calls=[ToolCall("c1", "echo", {"text": "hi"})]),
            ModelReply(content="Understood."),
        ]
    )
    agent = Agent(
        model,
        [make_echo()],
        permission_policy=DenyAll(reason="no tools today"),
        max_steps=3,
    )

    result = agent.run("Try it.")

    assert "permission denied" in result.messages[-2]["content"]
    assert "no tools today" in result.messages[-2]["content"]
    assert result.output == "Understood."


def test_unknown_tool_names_do_not_crash_the_loop() -> None:
    model = FakeModel(
        [
            ModelReply(tool_calls=[ToolCall("c1", "nope", {"text": "x"})]),
            ModelReply(content="Fine."),
        ]
    )
    agent = Agent(model, [make_echo()], permission_policy=AllowAll(), max_steps=3)

    result = agent.run("Use a missing tool.")

    assert "unknown tool 'nope'" in result.messages[-2]["content"]
    assert "echo" in result.messages[-2]["content"]
    assert result.status == "completed"


def test_tool_execution_failure_is_reported_to_the_model() -> None:
    def explode() -> str:
        raise RuntimeError("disk on fire")

    broken = Tool(
        name="boom",
        description="Always fails.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=explode,
    )
    model = FakeModel(
        [
            ModelReply(tool_calls=[ToolCall("c1", "boom", {})]),
            ModelReply(content="Fallback answer."),
        ]
    )
    agent = Agent(model, [broken], permission_policy=AllowAll(), max_steps=3)

    result = agent.run("Call the broken tool.")

    assert result.status == "completed"
    assert "Error: 'boom' failed" in result.messages[-2]["content"]
    assert "disk on fire" in result.messages[-2]["content"]
    assert result.output == "Fallback answer."


def test_max_steps_stops_the_loop() -> None:
    calls = [
        ModelReply(tool_calls=[ToolCall(f"c{i}", "echo", {"text": "x"})])
        for i in range(5)
    ]
    agent = Agent(FakeModel(calls), [make_echo()], permission_policy=AllowAll(), max_steps=3)

    result = agent.run("Loop forever.")

    assert result.status == "max_steps"
    assert result.steps == 3
    assert len(model_calls(result)) == 3


def model_calls(result: Any) -> list[dict[str, Any]]:
    return [message for message in result.messages if message["role"] == "assistant"]


def test_model_failure_becomes_model_error() -> None:
    agent = Agent(
        FakeModel([]),
        [make_echo()],
        permission_policy=AllowAll(),
        max_steps=3,
    )

    result = agent.run("Anything.")

    assert result.status == "model_error"
    assert "Model call failed" in result.output


def test_tracer_receives_tool_lifecycle_events() -> None:
    recorder = Recorder()
    model = FakeModel(
        [
            ModelReply(tool_calls=[ToolCall("c1", "echo", {"text": "hi"})]),
            ModelReply(content="Done."),
        ]
    )
    agent = Agent(
        model,
        [make_echo()],
        permission_policy=AllowAll(),
        max_steps=3,
        tracer=recorder,
    )

    agent.run("Trace it.")

    kinds = [event["type"] for event in recorder.events]
    assert kinds == [
        "user_message",
        "tool_call",
        "tool_result",
        "model_reply",
        "finish",
    ]


def test_tool_arguments_are_json_encoded_in_assistant_message() -> None:
    model = FakeModel(
        [
            ModelReply(tool_calls=[ToolCall("c1", "echo", {"text": "hi"})]),
            ModelReply(content="Done."),
        ]
    )
    agent = Agent(model, [make_echo()], permission_policy=AllowAll(), max_steps=3)

    result = agent.run("Go.")

    assistant_call = result.messages[1]["tool_calls"][0]
    assert assistant_call["function"]["name"] == "echo"
    assert assistant_call["function"]["arguments"] == '{"text": "hi"}'


def test_consequential_policy_routes_side_effects_to_the_interactive_policy() -> None:
    seen: list[str] = []

    def interactive(tool: Tool, arguments: dict[str, Any]) -> PermissionDecision:
        seen.append(tool.name)
        return PermissionDecision(allowed=True, reason="asked the human")

    read_only = Tool(
        name="peek",
        description="Read something.",
        input_schema={"type": "object", "properties": {}},
        handler=lambda: "peeked",
    )
    risky = Tool(
        name="destroy",
        description="Delete something.",
        input_schema={"type": "object", "properties": {}},
        handler=lambda: "boom",
        consequential=True,
    )
    policy = ConsequentialPolicy(interactive=CallbackPolicy(interactive))

    assert policy.decide(read_only, {}).allowed is True
    assert policy.decide(risky, {}).reason == "asked the human"
    assert seen == ["destroy"]


def test_read_only_calls_in_one_reply_run_in_parallel_in_order() -> None:
    slow = Tool(
        name="slow",
        description="Return the text after a tiny delay.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        handler=lambda text: f"{text}:done",
    )
    model = FakeModel(
        [
            ModelReply(
                tool_calls=[
                    ToolCall("c1", "slow", {"text": "a"}),
                    ToolCall("c2", "slow", {"text": "b"}),
                    ToolCall("c3", "slow", {"text": "c"}),
                ]
            ),
            ModelReply(content="All done."),
        ]
    )
    agent = Agent(model, [slow], permission_policy=AllowAll(), max_steps=3)

    result = agent.run("Fan out.")

    calls = [m for m in result.messages if m["role"] == "tool"]
    assert [message["content"] for message in calls] == [
        "a:done",
        "b:done",
        "c:done",
    ]
    assert [message["tool_call_id"] for message in calls] == ["c1", "c2", "c3"]


def test_consequential_calls_stay_serial() -> None:
    order: list[str] = []

    def append(text: str) -> str:
        order.append(text)
        return f"{text}:done"

    tool = Tool(
        name="risky",
        description="Touch state, must not run concurrently.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        handler=append,
        consequential=True,
    )
    model = FakeModel(
        [
            ModelReply(
                tool_calls=[
                    ToolCall("c1", "risky", {"text": "a"}),
                    ToolCall("c2", "risky", {"text": "b"}),
                ]
            ),
            ModelReply(content="Serialized."),
        ]
    )
    agent = Agent(model, [tool], permission_policy=AllowAll(), max_steps=3)

    agent.run("Touch state twice.")

    assert order == ["a", "b"]


def test_oversized_tool_output_is_clipped() -> None:
    model = FakeModel(
        [
            ModelReply(tool_calls=[ToolCall("c1", "echo", {"text": "abcdefghij"})]),
            ModelReply(content="Clipped."),
        ]
    )
    agent = Agent(
        model,
        [make_echo()],
        permission_policy=AllowAll(),
        max_steps=3,
        max_tool_chars=5,
    )

    result = agent.run("Shout.")

    tool_message = [m for m in result.messages if m["role"] == "tool"][0]
    assert tool_message["content"] == "abcde\n... [truncated 5 chars]"


def test_whitelist_policy_falls_back_to_denial() -> None:
    echo = make_echo()
    policy = WhitelistPolicy(names=["echo"])

    assert policy.decide(echo, {"text": "x"}).allowed is True
    assert policy.decide(make_tool_named("bash"), {}).allowed is False


def make_tool_named(name: str) -> Tool:
    return Tool(
        name=name,
        description="Something else.",
        input_schema={"type": "object", "properties": {}},
        handler=lambda: "",
    )
