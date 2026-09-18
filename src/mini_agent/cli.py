"""Command line entry point: python -m mini_agent.cli "question"."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .agent import Agent
from .fake_model import FakeModel
from .models import OpenAICompatibleModel, load_replies
from .permissions import AllowAll, AskUserPolicy, ConsequentialPolicy
from .tools import build_default_tools
from .trace import ConsoleTracer, render_trace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mini-agent",
        description="Run one Mini Agent turn against the workspace.",
    )
    parser.add_argument("query", help="The user question to answer.")
    parser.add_argument(
        "--root",
        default=".",
        help="Workspace root the file tools are confined to (default: cwd).",
    )
    parser.add_argument(
        "--max-steps", type=int, default=8, help="Model-call budget (default: 8)."
    )
    parser.add_argument(
        "--trust-all",
        action="store_true",
        help="Skip confirmation prompts, even for consequential tools.",
    )
    parser.add_argument(
        "--no-trace", action="store_true", help="Hide the per-step trace output."
    )
    parser.add_argument(
        "--show-messages",
        action="store_true",
        help="Print the final provider-neutral message history.",
    )

    model = parser.add_argument_group("model")
    model.add_argument(
        "--model-backend",
        choices=("openai", "script", "none"),
        default="openai",
        help="Where replies come from (default: openai).",
    )
    model.add_argument("--model-name", default="gpt-4o-mini")
    model.add_argument("--base-url", default="https://api.openai.com/v1")
    model.add_argument("--api-key", default=None)
    model.add_argument("--api-key-env", default="OPENAI_API_KEY")
    model.add_argument(
        "--script",
        default=None,
        help="With --model-backend script: JSON file of canned replies.",
    )
    return parser


def build_model(args: argparse.Namespace) -> Any:
    if args.model_backend == "script":
        if not args.script:
            raise SystemExit("--model-backend script requires --script replies.json")
        return FakeModel(load_replies(args.script))
    if args.model_backend == "none":
        return FakeModel([])
    return OpenAICompatibleModel(
        model=args.model_name,
        api_key=args.api_key,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
    )


def build_permission_policy(args: argparse.Namespace) -> Any:
    if args.trust_all:
        return AllowAll()
    return ConsequentialPolicy(interactive=AskUserPolicy())


SYSTEM_PROMPT = (
    "You are Mini Agent. Answer with tools when the workspace holds the answer, "
    "otherwise answer directly. Paths are relative to the workspace root. "
    "Ask for at most one round of tools before answering."
)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    registry = build_default_tools(args.root)
    tracer = ConsoleTracer(verbose=not args.no_trace)
    policy = build_permission_policy(args)

    try:
        model = build_model(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    agent = Agent(
        model,
        list(registry),
        permission_policy=policy,
        max_steps=args.max_steps,
        system_prompt=SYSTEM_PROMPT,
        tracer=tracer,
    )

    result = agent.run(args.query)

    print()
    if args.show_messages:
        print(render_trace(result))
        print()
    print(f"status={result.status} steps={result.steps}")
    if result.output:
        print(result.output)
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
