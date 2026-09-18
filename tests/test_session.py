from __future__ import annotations

import json

import pytest

from mini_agent import (
    Agent,
    AllowAll,
    FakeModel,
    ModelReply,
    SessionError,
    SessionStore,
    Tool,
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


def test_save_and_load_roundtrip(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]

    path = store.save("demo", messages, status="completed", steps=2)

    assert path.exists()
    assert store.load("demo") == messages
    assert store.meta("demo")["status"] == "completed"
    assert store.meta("demo")["steps"] == 2


def test_list_and_delete(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.save("first", [{"role": "user", "content": "a"}])
    store.save("second", [{"role": "user", "content": "b"}])

    assert [record.session_id for record in store.list()] == ["first", "second"]

    store.delete("first")
    assert not store.exists("first")
    assert [record.session_id for record in store.list()] == ["second"]


def test_missing_session_is_reported(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")

    with pytest.raises(SessionError):
        store.load("nope")


def test_unsafe_session_id_is_rejected(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")

    for bad in ("../escape", "a/b", "", "with space"):
        with pytest.raises(SessionError):
            store.save(bad, [])


def test_corrupt_session_file_is_reported(tmp_path) -> None:
    directory = tmp_path / "sessions"
    directory.mkdir()
    (directory / "broken.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(SessionError):
        SessionStore(directory).load("broken")


def test_a_run_can_be_resumed_from_a_saved_history(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")

    first_model = FakeModel([ModelReply(content="First answer.")])
    first = Agent(first_model, [make_echo()], permission_policy=AllowAll(), max_steps=3)
    first_result = first.run("What is the workspace?")
    store.save("demo", first_result.messages, status=first_result.status)

    restored = store.load("demo")
    second_model = FakeModel([ModelReply(content="Second answer.")])
    second = Agent(
        second_model, [make_echo()], permission_policy=AllowAll(), max_steps=3
    )

    second_result = second.run("And now?", history=restored)

    # The previous turn is still there, the new question is appended.
    assert second_result.messages[0]["content"] == "What is the workspace?"
    assert second_result.messages[-1]["content"] == "Second answer."
    # The model really saw the restored history, not just this turn.
    assert len(second_model.requests[0]["messages"]) == len(restored) + 1


def test_saved_history_is_plain_json(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.save("demo", [{"role": "user", "content": "hi"}], status="completed")

    payload = json.loads(store.path("demo").read_text(encoding="utf-8"))

    assert payload["session_id"] == "demo"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert payload["meta"]["status"] == "completed"
