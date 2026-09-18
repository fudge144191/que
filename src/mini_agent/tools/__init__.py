"""Tool registration centre and the built-in tool bundle."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path

from ..contracts import Tool
from .filesystem import build_filesystem_tools
from .search import build_search_tools
from .system import build_system_tools

Bundles = tuple[list[Tool], ...]


class ToolRegistry:
    """Name -> Tool lookup used by the Agent to resolve model requests."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        self.register_all(tools)

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def register_all(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def require(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(name)
        return tool

    def specs(self) -> list[dict[str, object]]:
        return [tool.as_model_spec() for tool in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def as_dict(self) -> Mapping[str, Tool]:
        return dict(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)


def build_default_tools(root: str | Path = ".") -> ToolRegistry:
    """Register the three bundled categories: filesystem, search, system."""
    return ToolRegistry(
        [
            *build_filesystem_tools(root),
            *build_search_tools(root),
            *build_system_tools(),
        ]
    )


def tools_from_iterable(tools: Sequence[Tool] | Mapping[str, Tool] | ToolRegistry) -> list[Tool]:
    """Normalise the constructor argument into a flat list."""
    if isinstance(tools, ToolRegistry):
        return list(tools)
    if isinstance(tools, Mapping):
        return list(tools.values())
    return list(tools)
