"""The Agent Loop (Harness).

The loop is provider neutral: it grows one message history, advertises tool
specs, validates and authorises every call, feeds results back, and stops on a
final answer, ``max_steps``, or an unrecoverable model error.

Two optional behaviours live here as well: read-only calls from the same reply
can run in parallel, and oversized tool output is clipped so one noisy tool
cannot blow up the context window.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .contracts import (
    ModelClient,
    PermissionPolicy,
    RunResult,
    RunStatus,
    Tool,
    ToolCall,
)
from .tools import ToolRegistry, tools_from_iterable
from .trace import Tracer, TraceEvent
from .validation import validate_arguments

_DEFAULT_AVAILABLE = "(no tools registered)"


@dataclass
class _CallPlan:
    """One tool call after validation and authorisation, ready to execute."""

    call: ToolCall
    index: int
    arguments: dict[str, Any]
    tool: Tool | None = None
    settled: str | None = None
    event: str | None = None

    @property
    def pending(self) -> bool:
        return self.settled is None


class Agent:
    """Student implementation entry point.

    Keep this constructor and ``run`` signature compatible with the public
    tests. You may split the implementation into more modules.
    """

    def __init__(
        self,
        model: ModelClient,
        tools: Sequence[Tool],
        permission_policy: PermissionPolicy,
        max_steps: int = 8,
        *,
        system_prompt: str | None = None,
        tracer: Tracer | None = None,
        parallel_tools: bool = True,
        max_workers: int = 4,
        max_tool_chars: int = 8000,
    ) -> None:
        self.model = model
        self.tools = tools_from_iterable(tools)
        self.registry = ToolRegistry(self.tools)
        self.permission_policy = permission_policy
        self.max_steps = max_steps
        self.system_prompt = system_prompt
        self.tracer = tracer
        self.parallel_tools = parallel_tools
        self.max_workers = max_workers
        self.max_tool_chars = max_tool_chars

    def run(self, query: str) -> RunResult:
        """Run one user turn to completion."""
        messages: list[dict[str, Any]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": query})

        specs = self.registry.specs()
        self._emit({"type": "user_message", "content": query, "step": 0})

        steps = 0
        output = ""
        status: RunStatus = "max_steps"

        while steps < self.max_steps:
            try:
                reply = self.model.complete(
                    messages=deepcopy(messages), tools=deepcopy(specs)
                )
            except Exception as exc:  # A broken provider must not kill the run.
                note = f"Model call failed: {exc}"
                messages.append({"role": "assistant", "content": note})
                self._emit({"type": "model_error", "content": note, "step": steps})
                return RunResult("model_error", note, messages, steps)

            steps += 1
            assistant: dict[str, Any] = {"role": "assistant", "content": reply.content}
            if reply.tool_calls:
                assistant["tool_calls"] = [
                    self._as_assistant_call(call) for call in reply.tool_calls
                ]
            messages.append(assistant)

            if reply.content:
                output = reply.content
                self._emit(
                    {"type": "model_reply", "content": reply.content, "step": steps}
                )

            if not reply.tool_calls:
                status = "completed"
                break

            self._handle_calls(reply.tool_calls, messages, step=steps)
        else:
            status = "max_steps"

        self._emit(
            {
                "type": "finish",
                "status": status,
                "steps": steps,
                "output": output,
                "step": steps,
            }
        )
        return RunResult(status, output, messages, steps)

    # -- one reply's worth of tool calls ------------------------------------

    def _handle_calls(
        self,
        calls: Sequence[ToolCall],
        messages: list[dict[str, Any]],
        step: int,
    ) -> None:
        plans = [self._plan(call, index, step) for index, call in enumerate(calls)]
        self._execute_plans(plans, step)
        for plan in plans:
            self._emit(
                {
                    "type": plan.event or "tool_result",
                    "name": plan.call.name,
                    "content": plan.settled or "",
                    "step": step,
                }
            )
            self._append_result(plan.call, messages, plan.settled or "")

    def _plan(self, call: ToolCall, index: int, step: int) -> _CallPlan:
        arguments = (
            dict(call.arguments) if isinstance(call.arguments, Mapping) else {}
        )
        self._emit(
            {
                "type": "tool_call",
                "name": call.name,
                "arguments": arguments,
                "step": step,
            }
        )
        plan = _CallPlan(call=call, index=index, arguments=arguments)

        tool = self.registry.get(call.name)
        if tool is None:
            plan.settled = self._unknown_tool_error(call.name)
            plan.event = "tool_error"
            return plan

        errors = validate_arguments(arguments, tool.input_schema)
        if errors:
            plan.settled = "Error: invalid arguments: " + "; ".join(errors)
            plan.event = "validation_error"
            return plan

        decision = self.permission_policy.decide(tool, arguments)
        if not decision.allowed:
            reason = decision.reason or "no reason given"
            plan.settled = f"Error: permission denied for '{call.name}': {reason}"
            plan.event = "permission_denied"
            return plan

        plan.tool = tool
        return plan

    def _execute_plans(self, plans: list[_CallPlan], step: int) -> None:
        pending = [plan for plan in plans if plan.pending and plan.tool is not None]
        read_only = [plan for plan in pending if not plan.tool.consequential]
        consequential = [plan for plan in pending if plan.tool.consequential]

        results: dict[int, str] = {}
        if self.parallel_tools and len(read_only) > 1:
            workers = min(self.max_workers, len(read_only))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(self._execute, plan) for plan in read_only]
                for plan, future in zip(read_only, futures):
                    results[plan.index] = future.result()
        else:
            for plan in read_only:
                results[plan.index] = self._execute(plan)

        # Side effects stay serial and in the order the model asked for.
        for plan in consequential:
            results[plan.index] = self._execute(plan)

        for plan in pending:
            plan.settled = results[plan.index]

    def _execute(self, plan: _CallPlan) -> str:
        tool = plan.tool
        assert tool is not None  # Guarded by _execute_plans.
        try:
            result = tool.handler(**plan.arguments)
            content = (
                result
                if isinstance(result, str)
                else json.dumps(result, ensure_ascii=False, default=str)
            )
        except TypeError as exc:
            return f"Error: '{tool.name}' received bad arguments: {exc}"
        except Exception as exc:
            return f"Error: '{tool.name}' failed: {exc}"
        return self._clip(content)

    def _clip(self, text: str) -> str:
        limit = self.max_tool_chars
        if limit and len(text) > limit:
            return f"{text[:limit]}\n... [truncated {len(text) - limit} chars]"
        return text

    # -- helpers -------------------------------------------------------------

    def _as_assistant_call(self, call: ToolCall) -> dict[str, Any]:
        return {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False),
            },
        }

    def _unknown_tool_error(self, name: str) -> str:
        available = ", ".join(sorted(self.registry.names())) or _DEFAULT_AVAILABLE
        return f"Error: unknown tool '{name}'. Available tools: {available}"

    @staticmethod
    def _append_result(
        call: ToolCall, messages: list[dict[str, Any]], content: str
    ) -> None:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": content,
            }
        )

    def _emit(self, event: TraceEvent) -> None:
        if self.tracer is not None:
            self.tracer(event)
