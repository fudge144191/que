"""File tools, all confined to a sandbox root."""

from __future__ import annotations

from pathlib import Path

from ..contracts import Tool

MAX_BYTES = 512 * 1024


class ToolError(Exception):
    """Raised by a handler when the call cannot be fulfilled."""


def _sandbox(root: Path, raw: str) -> Path:
    """Resolve ``raw`` inside ``root`` and refuse anything that escapes.

    ``resolve()`` also collapses ``..`` segments and follows symlinks, so a link
    inside the workspace that points outside lands outside and is rejected here.
    """
    root_resolved = root.resolve()
    candidate = Path(raw).expanduser()
    target = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = target.resolve()
    except OSError as exc:
        raise ToolError(f"cannot resolve path {raw!r}: {exc}") from exc
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ToolError(f"path escapes the workspace: {raw}")
    return resolved


def build_filesystem_tools(root: str | Path) -> list[Tool]:
    base = Path(root).resolve()

    def read_file(path: str, encoding: str = "utf-8") -> str:
        target = _sandbox(base, path)
        if not target.exists():
            raise ToolError(f"no such file: {path}")
        if target.is_dir():
            raise ToolError(f"{path} is a directory, use list_dir instead")
        size = target.stat().st_size
        if size > MAX_BYTES:
            raise ToolError(f"{path} is too large ({size} bytes > {MAX_BYTES})")
        try:
            return target.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            raise ToolError(f"cannot decode {path}: {exc}") from exc

    def list_dir(path: str = ".") -> str:
        target = _sandbox(base, path)
        if not target.exists():
            raise ToolError(f"no such directory: {path}")
        if not target.is_dir():
            raise ToolError(f"{path} is not a directory")
        entries = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name))
        if not entries:
            return "(empty directory)"
        lines = [
            f"[{'dir' if entry.is_dir() else 'file'}] "
            f"{entry.relative_to(base).as_posix()}"
            for entry in entries
        ]
        return "\n".join(lines)

    def write_file(path: str, content: str, append: bool = False) -> str:
        target = _sandbox(base, path)
        if target.is_dir():
            raise ToolError(f"{path} is a directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.open("a" if append else "w", encoding="utf-8").write(content)
        action = "appended to" if append else "wrote"
        return f"{action} {target.relative_to(base).as_posix()} ({len(content)} chars)"

    def file_exists(path: str) -> str:
        target = _sandbox(base, path)
        kind = "directory" if target.is_dir() else "file" if target.is_file() else "missing"
        return f"{path}: {kind}"

    return [
        Tool(
            name="read_file",
            description="Read a UTF-8 text file inside the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative or absolute path.",
                        "minLength": 1,
                    },
                    "encoding": {"type": "string", "default": "utf-8"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=read_file,
        ),
        Tool(
            name="list_dir",
            description=(
                "List entries of a directory inside the workspace. "
                "Each line is '[dir] name' or '[file] name'."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory to list, default '.'.",
                        "default": ".",
                    },
                },
                "additionalProperties": False,
            },
            handler=list_dir,
        ),
        Tool(
            name="write_file",
            description="Write or append text to a file inside the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                    "append": {"type": "boolean", "default": False},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=write_file,
            consequential=True,
        ),
        Tool(
            name="file_exists",
            description="Check whether a path exists and what kind of entry it is.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "minLength": 1}},
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=file_exists,
        ),
    ]
