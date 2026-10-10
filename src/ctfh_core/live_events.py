"""Bounded public activity events, independent of reports and verdict evidence."""

from __future__ import annotations

import json
import re
import shlex
import time
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Callable


EVENT_LIMIT = 5000
EVENT_KINDS = {
    "turn.started", "turn.finished", "turn.failed", "turn.interrupted",
    "worker.stopped", "challenge.solved", "submission.started", "submission.verdict",
    "agent.message", "agent.progress", "plan.updated", "checkpoint",
    "tool.started", "tool.completed", "model.changed",
    "approval.requested", "approval.answered",
}
_sink: ContextVar[Callable[[str, str], None] | None] = ContextVar("live_event_sink", default=None)
_usage_sink: ContextVar[Callable[[int, int, int], None] | None] = ContextVar(
    "live_usage_sink", default=None
)
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")
_SECRET = re.compile(
    r'''(?ix)(["']?(?:password|passwd|pwd|token|api[_-]?key|secret|authorization|cookie|비밀번호)["']?\s*[:=]\s*)'''
    r'''(?:"[^"\n]*(?:"|$)|'[^'\n]*(?:'|$)|[^\s,;]+)'''
)


def public_text(value: object, limit: int = 1200) -> str:
    """Best-effort redaction for public prose; never pass tool args/output here."""
    if not isinstance(value, str):
        return ""
    value = _ANSI.sub("", value[:16000])
    value = "".join(c if c in "\n\t" or not unicodedata.category(c).startswith("C") else "" for c in value)
    value = re.sub(r"(?i)\b(?:bearer\s+|sk-)[a-z0-9_.-]+", "[REDACTED]", value)
    value = _SECRET.sub(r"\1[REDACTED]", value)
    value = re.sub(r"(?i)\b[a-z0-9_-]{2,40}\{[^}\n]*(?:\}|$)", "[FLAG REDACTED]", value)
    value = " ".join(value.split())
    return value[:limit] + ("…" if len(value) > limit else "")


@contextmanager
def capture_events(sink: Callable[[str, str], None], *,
                   usage_sink: Callable[[int, int, int], None] | None = None):
    """Task-local sink: parallel turns cannot overwrite one another's callback."""
    token = _sink.set(sink)
    usage_token = _usage_sink.set(usage_sink)
    try:
        yield
    finally:
        _usage_sink.reset(usage_token)
        _sink.reset(token)


def emit(kind: str, message: str) -> None:
    sink = _sink.get()
    if sink is not None and kind in EVENT_KINDS:
        sink(kind, public_text(message))


def _command_label(command: object, depth: int = 0) -> str:
    """Executable/action labels only; arguments can contain credentials or flags."""
    if depth > 3:
        return "shell"
    try:
        args = shlex.split(command) if isinstance(command, str) else command
    except ValueError:
        return "shell"
    if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
        return "shell"
    if Path(args[0]).name in {"bash", "zsh", "sh", "dash"} and len(args) == 3 and args[1] in {"-c", "-lc"}:
        return _command_label(args[2], depth + 1)
    if args[:2] == ["rtk", "proxy"]:
        args = args[2:]
    elif args[:1] == ["rtk"]:
        args = args[1:]
    if args[:1] == ["env"]:
        args = args[1:]
        while args and "=" in args[0]:
            args = args[1:]
    if not args:
        return "shell"
    if "ctfh_core" in args and "browser" in args and "--" in args:
        action = args[args.index("--") + 1:][:1]
        label = "chrome-use " + (action[0] if action else "browser")
    else:
        label = Path(args[0]).name
        if label == "chrome-use":
            # Flags/session names may precede the action. Only known actions are shown.
            action = next((a for a in args[1:] if a in {
                "open", "snapshot", "read", "click", "fill", "get", "eval", "auth", "session",
            }), "browser")
            label += " " + action
    return public_text(label, 100)


def publish_item(item: object, *, completed: bool) -> None:
    if not isinstance(item, dict):
        return
    kind = item.get("type")
    if kind in {"agentMessage", "agent_message"}:
        text = item.get("text")
        if (completed and item.get("phase") in {None, "commentary"}
                and isinstance(text, str) and not text.lstrip().startswith(("{", "["))):
            emit("agent.message", text)
        return
    if kind in {"commandExecution", "command_execution"}:
        label = _command_label(item.get("command"))
    elif kind in {"mcpToolCall", "mcp_tool_call", "dynamicToolCall"}:
        label = "MCP " + public_text(str(item.get("server", "")), 80) + "/" + public_text(str(item.get("tool", "tool")), 80)
    elif kind in {"fileChange", "file_change"}:
        label = "파일 변경"
    elif kind in {"webSearch", "web_search"}:
        label = "웹 검색"
    elif kind == "todo_list" and completed:
        publish_plan(item.get("items"))
        return
    else:
        # Reasoning, input, raw response items and unknown types stay private.
        return
    if completed:
        status = item.get("status")
        if status in {"failed", "declined", "completed", "interrupted"}:
            label += " · " + status
        exit_code = item.get("exitCode", item.get("exit_code"))
        if isinstance(exit_code, int):
            label += f" · exit {exit_code}"
    emit("tool.completed" if completed else "tool.started", label)


def publish_plan(plan: object) -> None:
    if not isinstance(plan, list):
        return
    steps = []
    for item in plan[:8]:
        if isinstance(item, dict):
            step = public_text(item.get("step", item.get("text")), 150)
            status = item.get("status", "completed" if item.get("completed") else "pending")
            if step:
                steps.append(f"{public_text(status, 30)}: {step}")
    if steps:
        emit("plan.updated", " | ".join(steps))


def publish_exec_event(event: dict) -> None:
    if event.get("type") == "turn.completed":
        usage = event.get("usage")
        if isinstance(usage, dict):
            values = (usage.get("input_tokens"), usage.get("cached_input_tokens"),
                      usage.get("output_tokens"))
            if all(type(value) is int and value >= 0 for value in values):
                sink = _usage_sink.get()
                if sink is not None:
                    sink(*values)
    elif event.get("type") in {"item.started", "item.completed"}:
        publish_item(event.get("item"), completed=event["type"] == "item.completed")


def publish_app_event(message: dict) -> None:
    """Caller must first verify the notification's thread and turn identity."""
    params = message.get("params", {})
    if message.get("method") in {"item/started", "item/completed"}:
        publish_item(params.get("item"), completed=message["method"] == "item/completed")
    elif message.get("method") == "turn/plan/updated":
        publish_plan(params.get("plan"))
    elif message.get("method") == "model/rerouted":
        emit("model.changed", public_text(params.get("toModel"), 100))


def event_lines(store, *, limit: int = 50, challenge: str | None = None,
                follow: bool = False, as_json: bool = False):
    """Read a consistent initial tail, then advance the global cursor without gaps."""
    cursor = store.latest_event_id()
    records = store.events(limit=limit, challenge=challenge, through=cursor)
    while True:
        for record in records:
            if as_json:
                yield json.dumps(record, ensure_ascii=False)
            else:
                stamp = datetime.fromtimestamp(record["created_at"]).strftime("%H:%M:%S")
                target = public_text(record["challenge_key"] or record["role"], 100)
                turn = f"turn {record['turn_id']}" if record["turn_id"] is not None else "site"
                yield f"{stamp} [{target} · {turn}] {record['kind']}  {record['message']}"
        if not follow:
            return
        time.sleep(0.25)
        high_water = store.latest_event_id()
        # Retained history is bounded, so one polling batch can include it all.
        records = store.events(limit=EVENT_LIMIT, challenge=challenge,
                               after=cursor, through=high_water)
        cursor = high_water
