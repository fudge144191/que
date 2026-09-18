"""Save and restore a conversation so one session can span several runs.

The Agent itself stays stateless: ``run()`` still takes a query, and the caller
decides whether to hand back a history that was loaded from disk. That keeps the
Loop testable without touching the filesystem.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class SessionError(Exception):
    """Raised for unusable session ids, missing sessions or corrupt files."""


@dataclass
class SessionRecord:
    session_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


class SessionStore:
    """One JSON file per session, keyed by a filesystem-safe id."""

    def __init__(self, directory: str | Path = ".sessions") -> None:
        self.directory = Path(directory)

    def _path(self, session_id: str) -> Path:
        if not _SAFE_ID.match(session_id):
            raise SessionError(
                "invalid session id: use letters, digits, '_', '-' or '.' "
                f"(got {session_id!r})"
            )
        return self.directory / f"{session_id}.json"

    def path(self, session_id: str) -> Path:
        return self._path(session_id)

    def exists(self, session_id: str) -> bool:
        return self._path(session_id).exists()

    def save(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        **meta: Any,
    ) -> Path:
        target = self._path(session_id)
        payload = {
            "session_id": session_id,
            "meta": {
                **meta,
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            "messages": [dict(message) for message in messages],
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target

    def load(self, session_id: str) -> list[dict[str, Any]]:
        target = self._path(session_id)
        if not target.exists():
            raise SessionError(f"no such session: {session_id}")
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            raise SessionError(f"corrupt session file {target}: {exc}") from exc
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise SessionError(f"corrupt session file {target}: no message list")
        return [dict(message) for message in messages if isinstance(message, dict)]

    def meta(self, session_id: str) -> dict[str, Any]:
        target = self._path(session_id)
        if not target.exists():
            raise SessionError(f"no such session: {session_id}")
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            raise SessionError(f"corrupt session file {target}: {exc}") from exc
        meta = payload.get("meta")
        return dict(meta) if isinstance(meta, dict) else {}

    def delete(self, session_id: str) -> None:
        target = self._path(session_id)
        if not target.exists():
            raise SessionError(f"no such session: {session_id}")
        target.unlink()

    def list(self) -> list[SessionRecord]:
        if not self.directory.exists():
            return []
        records: list[SessionRecord] = []
        for path in sorted(self.directory.glob("*.json")):
            session_id = path.stem
            try:
                records.append(
                    SessionRecord(
                        session_id=session_id,
                        messages=self.load(session_id),
                        meta=self.meta(session_id),
                    )
                )
            except SessionError:
                # A half-written file must not break listing.
                records.append(SessionRecord(session_id=session_id, meta={}))
        records.sort(key=lambda record: record.meta.get("updated_at", ""))
        return records

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"SessionStore({str(self.directory)!r})"
