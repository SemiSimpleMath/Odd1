"""The task manager's inbox: any agent asks it a question (tasks_ask), and the manager answers "@name ..." into that
agent's own conversation (Claude Code's cross-session messages: it arrives as a new event the agent reacts to).

An asker is known by its Claude Code session: the MCP server or CLI walks up its parent processes to the Claude Code
process, whose registry file (~/.claude/sessions/<pid>.json) holds the session's id and name, the address other
sessions message it by. A role agent run by the runner is also known by its role; one that has stopped by the time
the answer comes gets it in its next run's prompt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .store import actor_from_env


def _sessions_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "sessions"


def _parent(pid: int) -> int | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[1])
    except (OSError, IndexError, ValueError):
        return None


def _alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


def own_session(pid: int | None = None, sessions: Path | None = None) -> dict | None:
    """The Claude Code session this process runs under: {id, name}, from the nearest ancestor with a registry file."""
    sessions = sessions or _sessions_dir()
    pid = pid or os.getpid()
    for _ in range(32):
        path = sessions / f"{pid}.json"
        if path.is_file():
            try:
                record = json.loads(path.read_text())
                return {"id": record["sessionId"], "name": record.get("name") or ""}
            except (OSError, ValueError, KeyError):
                return None
        pid = _parent(pid)
        if not pid or pid <= 1:
            return None
    return None


def caller() -> dict:
    """Who is asking: {actor, name, session}. name is the address a reply goes to: the session's name, else the
    role (runner agents), else the actor."""
    actor, session = actor_from_env(), own_session()
    name = (session or {}).get("name") or os.environ.get("ODD1_TASKS_AGENT") or actor
    return {"actor": actor, "name": name, "session": (session or {}).get("id")}


def live_name(session_id: str | None, sessions: Path | None = None) -> str | None:
    """The current name of a running session (names can change; the id does not), or None if it has ended."""
    if not session_id:
        return None
    sessions = sessions or _sessions_dir()
    for path in sessions.glob("*.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if record.get("sessionId") == session_id and _alive(int(record.get("pid", 0) or 0)):
            return record.get("name") or None
    return None


def delivery(question: dict, reply: dict, sessions: Path | None = None) -> dict:
    """How the manager hands its reply over: {to, message} to send with SendMessage when the asker's session is
    still running, else {to: None, note} (a role agent reads it in its next run's prompt)."""
    to = live_name(question.get("session"), sessions)
    quoted = question["text"] if len(question["text"]) <= 80 else question["text"][:77] + "..."
    message = (f"@{to or question['sender']} The task manager answers your message #{question['id']} "
               f"(“{quoted}”): {reply['text']}")
    if to:
        return {"to": to, "message": message}
    return {"to": None, "message": message,
            "note": "the asker's session has ended; a role agent gets this reply in its next run's prompt"}
