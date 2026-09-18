"""Permission policies injected into the Agent.

Every policy implements :class:`~mini_agent.contracts.PermissionPolicy`, so the
Agent never blocks on ``input()`` unless a policy explicitly chooses to.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from typing import Any, Callable

from .contracts import PermissionDecision, PermissionPolicy, Tool

_YES = {"y", "yes"}
_NO = {"n", "no"}
_ALWAYS = {"a", "always"}


class BasePolicy(ABC):
    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"{type(self).__name__}()"

    @abstractmethod
    def decide(self, tool: Tool, arguments: Mapping[str, Any]) -> PermissionDecision:
        ...


class AllowAll(BasePolicy):
    """Approve every call. Use for tests or fully trusted single-shot runs."""

    def decide(self, tool: Tool, arguments: Mapping[str, Any]) -> PermissionDecision:
        return PermissionDecision(allowed=True, reason="policy allows every tool")


class DenyAll(BasePolicy):
    def __init__(self, reason: str = "policy denies every tool") -> None:
        self._reason = reason

    def decide(self, tool: Tool, arguments: Mapping[str, Any]) -> PermissionDecision:
        return PermissionDecision(allowed=False, reason=self._reason)


class WhitelistPolicy(BasePolicy):
    """Allow only the named tools; everything else goes to the fallback."""

    def __init__(
        self,
        names: Iterable[str],
        fallback: PermissionPolicy | None = None,
    ) -> None:
        self._names = set(names)
        self._fallback = fallback or DenyAll(
            reason="tool is not on the whitelist"
        )

    def decide(self, tool: Tool, arguments: Mapping[str, Any]) -> PermissionDecision:
        if tool.name in self._names:
            return PermissionDecision(allowed=True, reason="tool is whitelisted")
        return self._fallback.decide(tool, arguments)


class ConsequentialPolicy(BasePolicy):
    """Ask for side-effecting tools, auto-approve everything else.

    This matches how real harnesses behave: read-only tools run freely, the ones
    flagged ``consequential=True`` need an explicit human decision.

    Example:
        ConsequentialPolicy(interactive=AskUserPolicy())
    """

    def __init__(
        self,
        interactive: PermissionPolicy,
        automatic: PermissionPolicy | None = None,
    ) -> None:
        self._interactive = interactive
        self._automatic = automatic or AllowAll()

    def decide(self, tool: Tool, arguments: Mapping[str, Any]) -> PermissionDecision:
        target = self._interactive if tool.consequential else self._automatic
        return target.decide(tool, arguments)


class AskUserPolicy(BasePolicy):
    """Prompt the operator; ``a`` remembers the approval for this session.

    Reads and writes through injected callables so tests stay non-interactive.
    """

    def __init__(
        self,
        read: Callable[[str], str] | None = None,
        write: Callable[[str], None] | None = None,
    ) -> None:
        self._read = read or input
        self._write = write or print
        self._remembered: set[str] = set()

    @staticmethod
    def describe(tool: Tool, arguments: Mapping[str, Any]) -> str:
        rendered = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
        kind = "consequential" if tool.consequential else "read-only"
        head = f"{tool.name}({rendered})" if rendered else f"{tool.name}()"
        return f"{head} [{kind}] {tool.description}".strip()

    def decide(self, tool: Tool, arguments: Mapping[str, Any]) -> PermissionDecision:
        if tool.name in self._remembered:
            return PermissionDecision(
                allowed=True, reason="already approved in this session"
            )

        raw = self._read(
            f"允许执行工具 {self.describe(tool, arguments)}? [y/N/a]: "
        )
        # Keep ASCII only: a piped or redirected stdin can arrive with a UTF-8
        # BOM that the console code page decodes into junk characters.
        answer = "".join(char for char in raw if char.isascii()).strip().lower()

        if answer in _ALWAYS:
            self._remembered.add(tool.name)
            return PermissionDecision(
                allowed=True, reason="approved for the rest of the session"
            )
        if answer in _YES:
            return PermissionDecision(allowed=True, reason="approved once")
        if answer in _NO or answer == "":
            return PermissionDecision(allowed=False, reason="rejected by the operator")

        self._write("未识别的输入，按拒绝处理（y/a 允许，n 拒绝）。")
        return PermissionDecision(allowed=False, reason="unrecognised answer")


class CallbackPolicy(BasePolicy):
    """Adapt a plain function into a policy, handy for tests and scripts."""

    def __init__(self, decide: Callable[[Tool, Mapping[str, Any]], PermissionDecision]) -> None:
        self._decide = decide

    def decide(self, tool: Tool, arguments: Mapping[str, Any]) -> PermissionDecision:
        return self._decide(tool, arguments)
