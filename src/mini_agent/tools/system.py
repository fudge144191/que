"""Computation and environment tools."""

from __future__ import annotations

import ast
import operator
from datetime import datetime
from typing import Any, Callable

from ..contracts import Tool

_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type, Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "pow": pow,
    "sum": sum,
    "len": len,
}


def evaluate_expression(expression: str) -> float:
    """Evaluate an arithmetic expression with a whitelisted AST walk."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid expression: {exc}") from exc

    def walk(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](walk(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return _BIN_OPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name not in _FUNCTIONS or node.keywords:
                raise ValueError(f"function not allowed: {name}")
            return _FUNCTIONS[name](*(walk(arg) for arg in node.args))
        if isinstance(node, ast.Name):
            raise ValueError(f"names are not allowed: {node.id}")
        raise ValueError(f"unsupported syntax: {type(node).__name__}")

    value = walk(tree)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("expression must evaluate to a number")
    return value


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(value)


def build_system_tools() -> list[Tool]:
    def calculator(expression: str) -> str:
        try:
            value = evaluate_expression(expression)
        except ZeroDivisionError:
            return "Error: division by zero"
        except ValueError as exc:
            return f"Error: {exc}"
        return _format_number(value)

    def now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def echo(text: str) -> str:
        return text

    return [
        Tool(
            name="calculator",
            description="Evaluate a pure arithmetic expression, e.g. '(2 + 3) * 4'.",
            input_schema={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "minLength": 1, "maxLength": 200},
                },
                "required": ["expression"],
                "additionalProperties": False,
            },
            handler=calculator,
        ),
        Tool(
            name="now",
            description="Return the current local date and time as ISO 8601.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=now,
        ),
        Tool(
            name="echo",
            description="Return the supplied text unchanged.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            handler=echo,
        ),
    ]
