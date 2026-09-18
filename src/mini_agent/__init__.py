"""Shared interfaces for the Mini Agent admission task."""

from .agent import Agent
from .context import compact_messages, estimate_chars
from .contracts import (
    ModelClient,
    ModelReply,
    PermissionDecision,
    PermissionPolicy,
    RunResult,
    RunStatus,
    Tool,
    ToolCall,
)
from .fake_model import FakeModel
from .models import OpenAICompatibleModel, load_replies
from .permissions import (
    AllowAll,
    AskUserPolicy,
    BasePolicy,
    CallbackPolicy,
    ConsequentialPolicy,
    DenyAll,
    WhitelistPolicy,
)
from .session import SessionError, SessionRecord, SessionStore
from .tools import ToolRegistry, build_default_tools
from .trace import ConsoleTracer, render_event, render_trace
from .validation import matches_type, type_name, validate_arguments, validate_instance

__all__ = [
    "Agent",
    "AllowAll",
    "AskUserPolicy",
    "BasePolicy",
    "CallbackPolicy",
    "compact_messages",
    "ConsequentialPolicy",
    "ConsoleTracer",
    "DenyAll",
    "estimate_chars",
    "FakeModel",
    "ModelClient",
    "ModelReply",
    "OpenAICompatibleModel",
    "PermissionDecision",
    "PermissionPolicy",
    "RunResult",
    "RunStatus",
    "SessionError",
    "SessionRecord",
    "SessionStore",
    "Tool",
    "ToolCall",
    "ToolRegistry",
    "WhitelistPolicy",
    "build_default_tools",
    "load_replies",
    "matches_type",
    "render_event",
    "render_trace",
    "type_name",
    "validate_arguments",
    "validate_instance",
]
