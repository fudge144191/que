"""Search tools: a small grep over text files in the workspace."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from ..contracts import Tool
from .filesystem import MAX_BYTES, ToolError, _sandbox

_TEXT_MAX_BYTES = MAX_BYTES


def _iter_files(root: Path, limit_files: int) -> Iterator[Path]:
    count = 0
    for entry in sorted(root.rglob("*")):
        if not entry.is_file():
            continue
        try:
            if entry.stat().st_size > _TEXT_MAX_BYTES:
                continue
        except OSError:
            continue
        yield entry
        count += 1
        if count >= limit_files:
            return


def build_search_tools(root: str | Path) -> list[Tool]:
    base = Path(root).resolve()

    def search_text(
        pattern: str,
        path: str = ".",
        case_sensitive: bool = False,
        max_results: int = 20,
    ) -> str:
        """Search files for a regex pattern and return ``file:line: content`` hits."""
        try:
            regex = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
        except re.error as exc:
            raise ToolError(f"invalid pattern {pattern!r}: {exc}") from exc

        target = _sandbox(base, path)
        scope = target if target.is_dir() else target.parent
        hits: list[str] = []
        try:
            for file_path in _iter_files(scope, limit_files=500):
                try:
                    text = file_path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for number, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        location = file_path.relative_to(base).as_posix()
                        hits.append(f"{location}:{number}: {line.strip()}")
                        if len(hits) >= max_results:
                            break
                if len(hits) >= max_results:
                    break
        except OSError as exc:
            raise ToolError(f"cannot search {path}: {exc}") from exc

        if not hits:
            return "no matches"
        return "\n".join(hits)

    def find_files(pattern: str, path: str = ".", max_results: int = 30) -> str:
        """Match workspace files by glob-style name pattern, e.g. ``**/*.md``."""
        target = _sandbox(base, path)
        scope = target if target.is_dir() else base
        try:
            matches = sorted(scope.glob(pattern))
        except (ValueError, NotImplementedError) as exc:
            raise ToolError(f"invalid glob {pattern!r}: {exc}") from exc

        if not matches:
            return "no matches"
        lines = [
            f"{'d' if item.is_dir() else 'f'} {item.relative_to(base).as_posix()}"
            for item in matches[:max_results]
        ]
        if len(matches) > max_results:
            lines.append(f"... and {len(matches) - max_results} more")
        return "\n".join(lines)

    return [
        Tool(
            name="search_text",
            description="Regex search across UTF-8 files under a directory.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "default": "."},
                    "case_sensitive": {"type": "boolean", "default": False},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 20,
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
            handler=search_text,
        ),
        Tool(
            name="find_files",
            description="Find workspace files whose names match a glob pattern.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "default": "."},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 30,
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
            handler=find_files,
        ),
    ]
