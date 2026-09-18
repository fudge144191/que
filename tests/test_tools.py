from __future__ import annotations

from uuid import uuid4

import pytest

from mini_agent import (
    Agent,
    AllowAll,
    FakeModel,
    ModelReply,
    ToolCall,
    ToolRegistry,
    Tool,
    build_default_tools,
    validate_arguments,
)
from mini_agent.tools.system import evaluate_expression


def test_default_tools_cover_three_categories_with_unique_names() -> None:
    registry = build_default_tools(".")

    assert len(registry) >= 3
    assert len(set(registry.names())) == len(registry.names())
    assert {"read_file", "search_text", "calculator"} <= set(registry.names())
    for spec in registry.specs():
        assert spec["description"]
        assert spec["input_schema"]["type"] == "object"


def test_registry_rejects_duplicate_names() -> None:
    tool = Tool(
        name="dup",
        description="d",
        input_schema={"type": "object", "properties": {}},
        handler=lambda: "",
    )
    registry = ToolRegistry([tool])

    try:
        registry.register(tool)
    except ValueError as exc:
        assert "duplicate tool name" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_file_tools_stay_inside_the_workspace(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("hello workspace", encoding="utf-8")
    model = FakeModel(
        [
            ModelReply(
                tool_calls=[
                    ToolCall("c1", "read_file", {"path": "note.txt"}),
                    ToolCall("c2", "read_file", {"path": "../outside.txt"}),
                ]
            ),
            ModelReply(content="Done."),
        ]
    )
    agent = Agent(model, list(build_default_tools(tmp_path)), permission_policy=AllowAll())

    result = agent.run("Read the note.")

    contents = [m["content"] for m in result.messages if m["role"] == "tool"]
    assert contents[0] == "hello workspace"
    assert "escapes the workspace" in contents[1]
    assert result.status == "completed"


def test_write_file_writes_inside_subdirectories(tmp_path) -> None:
    model = FakeModel(
        [
            ModelReply(
                tool_calls=[
                    ToolCall(
                        "c1",
                        "write_file",
                        {"path": "out/a.txt", "content": "stored"},
                    )
                ]
            ),
            ModelReply(content="stored."),
        ]
    )
    agent = Agent(model, list(build_default_tools(tmp_path)), permission_policy=AllowAll())

    result = agent.run("Write a file.")

    tool_message = [m for m in result.messages if m["role"] == "tool"][0]
    assert "wrote out/a.txt" in tool_message["content"]
    assert (tmp_path / "out" / "a.txt").read_text(encoding="utf-8") == "stored"


def test_search_tool_finds_lines(tmp_path) -> None:
    (tmp_path / "code.py").write_text("def alpha():\n    pass\n", encoding="utf-8")
    model = FakeModel(
        [
            ModelReply(tool_calls=[ToolCall("c1", "search_text", {"pattern": "def "})]),
            ModelReply(content="found."),
        ]
    )
    agent = Agent(model, list(build_default_tools(tmp_path)), permission_policy=AllowAll())

    result = agent.run("Find the function.")

    tool_message = [m for m in result.messages if m["role"] == "tool"][0]
    assert "code.py:1: def alpha():" in tool_message["content"]


def test_calculator_rejects_dangerous_syntax() -> None:
    assert evaluate_expression("2 * (3 + 4)") == 14

    for bad in ("__import__('os').system('echo hi')", "open('/etc/passwd')", "x + 1"):
        with pytest.raises(ValueError):
            evaluate_expression(bad)


def test_symlink_escape_is_rejected(tmp_path) -> None:
    outside = tmp_path.parent / f"secret-{uuid4().hex}.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):  # Windows without dev mode
        pytest.skip("symlinks are not available here")

    model = FakeModel(
        [
            ModelReply(tool_calls=[ToolCall("c1", "read_file", {"path": "link.txt"})]),
            ModelReply(content="Refused."),
        ]
    )
    agent = Agent(model, list(build_default_tools(tmp_path)), permission_policy=AllowAll())

    result = agent.run("Follow the link.")

    tool_message = [m for m in result.messages if m["role"] == "tool"][0]
    assert "escapes the workspace" in tool_message["content"]
    outside.unlink()


def test_validation_catches_schema_violations() -> None:
    schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["fast", "slow"]},
            "retries": {"type": "integer", "minimum": 0, "maximum": 5},
        },
        "required": ["mode"],
        "additionalProperties": False,
    }

    assert validate_arguments({"mode": "fast"}, schema) == []
    errors = validate_arguments({"mode": "turbo", "retries": 9, "extra": 1}, schema)
    joined = "\n".join(errors)
    assert "enum" in joined or "must be one of" in joined
    assert "must be <= 5" in joined
    assert "unexpected property" in joined
