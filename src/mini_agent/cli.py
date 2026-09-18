"""Command line entry point: python -m mini_agent.cli "question"."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .agent import Agent
from .fake_model import FakeModel
from .models import OpenAICompatibleModel, load_replies
from .permissions import AllowAll, AskUserPolicy, ConsequentialPolicy
from .session import SessionError, SessionStore
from .tools import build_default_tools
from .trace import ConsoleTracer, render_trace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mini-agent",
        description="Run one Mini Agent turn against the workspace.",
    )
    # Optional: `--list-sessions` is a valid standalone invocation.
    parser.add_argument("query", nargs="?", help="The user question to answer.")
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
    parser.add_argument(
        "--max-context-chars",
        type=int,
        default=20_000,
        help="Compact older messages past this budget; 0 disables (default: 20000).",
    )

    session = parser.add_argument_group("session")
    session.add_argument(
        "--session",
        default=None,
        metavar="ID",
        help="Resume this session if it exists, then save the new history to it.",
    )
    session.add_argument(
        "--sessions-dir",
        default=".sessions",
        help="Where session files live (default: .sessions).",
    )
    session.add_argument(
        "--list-sessions",
        action="store_true",
        help="List saved sessions and exit.",
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
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.list_sessions and not args.query:
        parser.error("a query is required unless --list-sessions is used")

    store = SessionStore(args.sessions_dir)
    if args.list_sessions:
        records = store.list()
        if not records:
            print(f"(no saved sessions in {store.directory})")
            return 0
        for record in records:
            meta = record.meta
            print(
                f"{record.session_id}  messages={len(record.messages)}"
                f"  steps={meta.get('steps', '-')}  status={meta.get('status', '-')}"
                f"  {meta.get('updated_at', '')}"
            )
        return 0

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
        max_context_chars=args.max_context_chars,
    )

    history: list[dict[str, Any]] | None = None
    if args.session and store.exists(args.session):
        try:
            history = store.load(args.session)
        except SessionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"resumed session {args.session} ({len(history)} messages)")

    result = agent.run(args.query, history=history)

    if args.session:
        path = store.save(
            args.session,
            result.messages,
            status=result.status,
            steps=result.steps,
            root=str(Path(args.root).resolve()),
        )
        print(f"session saved: {path} ({len(result.messages)} messages)")

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
