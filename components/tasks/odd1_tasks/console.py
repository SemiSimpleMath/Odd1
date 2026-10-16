"""The person's line to the task manager: a one-line status, and a Claude Code console that reports and acts on it.

    odd1-tasks status     one line: what each role is doing, what waits for the person, the next recurring run
    odd1-tasks console    Claude Code in <state>/console (its CLAUDE.md and settings: the status line, permissions)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from . import roles
from .store import Store, interval_seconds

CONSOLE = Path(os.environ.get("ODD1_TASKS_CONSOLE_SEED", Path(__file__).resolve().parent.parent / "console"))
OPENING = ("What is the task manager doing right now? In a few lines: what is running, what finished recently "
           "(result, cost), and what waits for me (proposed, blocked, parked).")


def _ago(seconds: float) -> str:
    minutes = max(round(seconds / 60), 0)
    return f"{minutes} min" if minutes < 90 else f"{round(minutes / 60)} h"


def _runner(role: str) -> str:
    """'' when the role's runner is running (or systemd is not there to ask), else its state."""
    if not shutil.which("systemctl"):
        return ""
    state = subprocess.run(["systemctl", "is-active", f"odd1-tasks-runner@{role}"], capture_output=True,
                           text=True).stdout.strip()
    return "" if state == "active" else state or "unknown"


def summary(store: Store, now: float | None = None) -> str:
    now = time.time() if now is None else now
    tasks = store.list()
    names = sorted(p.name for p in (store.state / "roles").iterdir() if p.is_dir()) \
        if (store.state / "roles").is_dir() else []
    parts = []
    for role in names:
        held = [t for t in tasks if t["claim_agent"] == role and t["status"] in ("claimed", "verifying")]
        down = _runner(role)
        if held:
            t = held[0]
            started = max((e["at"] for e in store.show(t["id"])["log"] if e["kind"] == "claim"), default=now)
            doing = "checking" if t["status"] == "verifying" else "on"
            parts.append(f"{role} {doing} #{t['id']} ({_ago(now - started)})")
        else:
            parts.append(f"{role} idle")
        if down:
            parts[-1] += f" (runner {down})"
        elif not roles.load(store, role)["enabled"]:
            parts[-1] += " (paused)"
    waiting = {s: sum(t["status"] == s for t in tasks) for s in ("proposed", "blocked", "parked")}
    parts += [f"{n} {s}" for s, n in waiting.items() if n]
    due = []
    for root in tasks:
        if root["schedule"] and root["approved_at"]:
            series = [t for t in tasks if t["id"] == root["id"] or t["parent_id"] == root["id"]]
            if not any(t["status"] not in ("done", "parked") for t in series):
                due.append(max(t["created_at"] for t in series) + interval_seconds(root["schedule"]))
    if due:
        parts.append(f"next recurring run in {_ago(min(due) - now)}")
    return "Tasks · " + " · ".join(parts or ["no roles yet"])


def seed(store: Store, source: Path = CONSOLE) -> Path:
    """The console folder, from the repository where files are missing (the person's edits stay)."""
    target = store.state / "console"
    roles.copy_missing(source, target)
    return target


def run(store: Store) -> int:
    """Replace this process with Claude Code in the console folder, opening with what the task manager is doing."""
    folder = seed(store)
    os.chdir(folder)
    env = {k: v for k, v in os.environ.items() if k != "ODD1_TASKS_AGENT"}  # the console speaks for the person
    command = ["claude", "-n", "Task manager", "--settings", str(folder / "settings.json"), OPENING]
    os.execvpe(command[0], command, env)
