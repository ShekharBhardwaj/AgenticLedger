"""Claude Code hook events become agent events: the part of the record the
proxy cannot see.

Claude Code runs hooks around everything it does on the machine: a tool
ran (with its input, its output, how long it took, whether it failed), a
permission was asked or denied, a sub-agent started or stopped, the
context was compacted, the session began or ended. Delivered as
http-type hooks, each one is a JSON POST to this ledger. The session id
in the hook is the same id the proxy records for the session's model
calls, so both halves of the record meet on it, and the hooks keep
flowing even when IT pins Claude Code's model traffic to a gateway the
ledger cannot sit in front of.

The ledger records and never steers: every hook gets an empty 200, so it
can never block, rewrite or delay what the agent does.
"""

import json
import uuid
from typing import Any, Optional

# One field of recorded content is cut at this many characters. Tool
# outputs can be whole files; the record keeps the head and says so.
CONTENT_CAP = 64_000

# Hook events the ledger records as agent events, and the outcome word
# each one carries. Anything else still lands, as "other".
_OUTCOME = {
    "PostToolUse": "ok",
    "PostToolUseFailure": "failed",
    "PermissionRequest": "asked",
    "PermissionDenied": "denied",
    "SessionStart": "started",
    "SessionEnd": "ended",
    "SubagentStart": "started",
    "SubagentStop": "stopped",
    "Stop": "stopped",
    "UserPromptSubmit": "prompt",
    "PreCompact": "compacting",
    "PostCompact": "compacted",
    "Notification": "notice",
    "TaskCreated": "created",
    "TaskCompleted": "completed",
    "FileChanged": "changed",
}

# Events the proxy already sees in full from the model traffic, or that
# fire too often to be worth a row: PreToolUse (PostToolUse carries the
# same input plus the result), PostToolBatch (a repeat of PostToolUse),
# MessageDisplay, UserPromptExpansion.
_SKIP = {"PreToolUse", "PostToolBatch", "MessageDisplay", "UserPromptExpansion",
         "InstructionsLoaded", "Setup", "StopFailure", "CwdChanged", "DirectoryAdded",
         "WorktreeCreate", "WorktreeRemove", "PreModelSwitch", "PostModelSwitch",
         "Elicitation", "ElicitationResult", "TeammateIdle", "ConfigChange"}


def _text(value: Any) -> Optional[str]:
    """Content as stored text, cut at the cap, with a note when cut."""
    if value is None:
        return None
    s = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(s) > CONTENT_CAP:
        return s[:CONTENT_CAP] + f"\n... [cut by the ledger at {CONTENT_CAP} characters]"
    return s


def _opt(value: Any) -> Optional[str]:
    return str(value) if isinstance(value, (str, int, float)) and str(value) != "" else None


def event_from_hook(body: dict, now: float) -> Optional[dict]:
    """One agent-event row from one hook POST, or None for events the
    ledger does not record."""
    if not isinstance(body, dict):
        return None
    name = _opt(body.get("hook_event_name"))
    if not name or name in _SKIP:
        return None
    session_id = _opt(body.get("session_id"))
    if not session_id:
        return None
    agent_id = _opt(body.get("agent_id"))
    tool_input = body.get("tool_input")
    tool_response = body.get("tool_response")
    detail: dict[str, Any] = {}
    for key in ("source", "reason", "trigger", "model", "agent_type", "notification_type",
                "message", "title", "stop_hook_active", "is_interrupt", "task_id",
                "task_subject", "permission_mode", "cwd", "prompt_id", "transcript_path",
                "agent_transcript_path", "custom_instructions", "file_path", "status"):
        if body.get(key) not in (None, ""):
            detail[key] = body[key]
    if name == "UserPromptSubmit" and body.get("prompt") is not None:
        detail["prompt"] = _text(body.get("prompt"))
    if name in ("Stop", "SubagentStop") and body.get("last_assistant_message") is not None:
        detail["last_assistant_message"] = _text(body.get("last_assistant_message"))
    if name == "PostCompact" and body.get("compact_summary") is not None:
        detail["compact_summary"] = _text(body.get("compact_summary"))
    if name == "PostToolUseFailure":
        detail["error"] = _text(body.get("error"))
    if name == "PermissionRequest" and body.get("permission_suggestions") is not None:
        detail["permission_suggestions"] = body.get("permission_suggestions")
    reason = _opt(body.get("reason")) or (_text(body.get("error")) if name == "PostToolUseFailure" else None)
    mcp = body.get("mcp_server")
    if isinstance(mcp, dict) and mcp.get("name"):
        detail["mcp_server"] = mcp.get("name")
    duration = body.get("duration_ms")
    try:
        duration_ms = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    return {
        "id": uuid.uuid4().hex,
        "session_id": session_id,
        "agent_id": agent_id,
        "agent_type": _opt(body.get("agent_type")),
        "event": name,
        "outcome": _OUTCOME.get(name, "other"),
        "tool_name": _opt(body.get("tool_name")),
        "tool_use_id": _opt(body.get("tool_use_id")),
        "tool_input": _text(tool_input),
        "tool_response": _text(tool_response),
        "reason": reason[:2000] if reason else None,
        "duration_ms": duration_ms,
        "prompt_id": _opt(body.get("prompt_id")),
        "cwd": _opt(body.get("cwd")),
        "source": "hook",
        "parent_id": None,   # placed by tool_use_id or time when the steps are built
        "detail": json.dumps(detail, default=str) if detail else None,
        "timestamp": now,
    }
