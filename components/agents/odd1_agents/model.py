"""The Agents app's model: health and settings of every agent system on Odd1, read and written without a window.

The app (app.py) and `odd1-agents status` show what this returns; every change goes through here, as the person:
tasks through the task store's own rules (odd1_tasks), role settings into the role's role.toml (comments kept),
Claude Code through its own CLI. Nothing here is a second copy of another component's state.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import tomllib
from datetime import datetime
from pathlib import Path

from odd1_tasks import attention, console, roles
from odd1_tasks.store import Store, actor_from_env

CARETAKER = Path(os.environ.get("ODD1_CARETAKER_STATE", "/var/lib/odd1/caretaker"))
AGENTS = Path(os.environ.get("ODD1_AGENTS", "/etc/odd1/agents.json"))
MCP_SERVERS = Path(os.environ.get("ODD1_MCP_SERVERS", "/etc/odd1/mcp-servers.json"))
WAITING = ("proposed", "blocked", "parked")
# What a person may set on a role, and the bounds that keep a typo from becoming a runaway budget.
LIMITS = {"runs_per_day": (0, 100), "max_turns": (1, 500), "max_minutes": (1, 240),
          "escalate_after_minutes": (10, 10080)}
TAG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _midnight(now: float) -> float:
    return datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def role_names(store: Store) -> list[str]:
    folder = store.state / "roles"
    return sorted(p.name for p in folder.iterdir() if (p / "role.toml").exists()) if folder.is_dir() else []


# -- Overview ---------------------------------------------------------------------------------------------------------
def overview(store: Store, now: float | None = None) -> dict:
    """Every role (on or paused, its runner, what it is doing, today's runs and cost), what waits for the person,
    today's totals, and the machine's health (caretaker)."""
    now = time.time() if now is None else now
    tasks = store.list()
    since = _midnight(now)
    ends = [(r["actor"].split(":", 1)[-1], json.loads(r["data"]), r["task_id"]) for r in store.db.execute(
        "SELECT actor, data, task_id FROM log WHERE kind = 'run-end' AND at >= ?", (since,))]
    results = [r["text"] for r in store.db.execute(
        "SELECT text FROM log WHERE kind = 'result' AND at >= ?", (since,))]
    rows = []
    for name in role_names(store):
        cfg = roles.load(store, name)
        held = next((t for t in tasks if t["claim_agent"] == name and t["status"] in ("claimed", "verifying")), None)
        mine = [data for role, data, _ in ends if role == name]
        rows.append({
            "name": name, "enabled": cfg["enabled"], "runner": console._runner(name) or "active",
            "doing": None if held is None else {"id": held["id"], "goal": held["goal"], "status": held["status"]},
            "runs_today": len(mine), "cost_today": round(sum(d.get("total_cost_usd") or 0 for d in mine), 2),
            "runs_per_day": cfg["runs_per_day"],
        })
    found = attention.items(store, now)
    return {
        "line": console.summary(store, now),
        "needs_you": attention.for_whom(found, attention.PERSON),
        "with_agents": [i for i in found if i["who"] != attention.PERSON],
        "roles": rows,
        "waiting": {s: sum(t["status"] == s for t in tasks) for s in WAITING},
        "today": {"runs": len(ends), "cost": round(sum(d.get("total_cost_usd") or 0 for _, d, _ in ends), 2),
                  "done": sum(r == "done" for r in results),
                  "failed": sum(r.startswith("check failed") for r in results)},
        "machine": machine(now),
    }


def machine(now: float | None = None, max_age: float = 180) -> dict:
    """The caretaker's last look: level (ok/elevated/high), its problems, and whether it is current."""
    now = time.time() if now is None else now
    try:
        health = json.loads((CARETAKER / "health.json").read_text())
        latest = json.loads((CARETAKER / "latest.json").read_text())
    except (OSError, ValueError):
        return {"state": "unknown", "level": "unknown", "problems": []}
    seen = health.get("last_success_at")
    age = now - datetime.fromisoformat(seen).timestamp() if seen else None
    state = "current" if health.get("ok") and age is not None and age <= max_age else "stale"
    return {"state": state, "level": latest.get("level", "unknown"), "problems": latest.get("problems", []),
            "age": age}


# -- Tasks ------------------------------------------------------------------------------------------------------------
FILTERS = {"Waiting for a decision": WAITING, "Running": ("claimed", "verifying"), "Ready": ("ready",),
           "Done": ("done",), "Declined or cancelled": ("declined", "cancelled"), "All": None}


def tasks(store: Store, view: str = "Waiting for a decision") -> list[dict]:
    """Tasks for one of FILTERS, newest first, each with the roles that may claim it and who it waits on now."""
    statuses = FILTERS[view]
    waiting_on = {i["task"]: i for i in attention.items(store)}
    held = {name: set(roles.load(store, name)["tags"]) for name in role_names(store)}
    out = []
    for t in reversed(store.list()):
        if statuses is None or t["status"] in statuses:
            t["roles"] = [name for name, tags in held.items() if set(t["tags"]) <= tags]
            item = waiting_on.get(t["id"])
            t["waiting_on"] = item["who"] if item else None
            out.append(t)
    return out


def approve(store: Store, task_id: int) -> dict:
    return store.approve(task_id, actor_from_env())


def decline(store: Store, task_id: int, reason: str) -> dict:
    return store.decline(task_id, actor_from_env(), reason)


def cancel(store: Store, task_id: int, reason: str) -> dict:
    """Close open work for good; a running agent is stopped at its next lease renewal."""
    return store.cancel(task_id, actor_from_env(), reason)


def stop(store: Store, task_id: int, reason: str = "stopped by the person") -> dict:
    """Block a claimed task: its runner stops the agent at the next lease renewal; approving it again resumes it."""
    return store.release(task_id, actor_from_env(), blocked=reason)


def note(store: Store, task_id: int, text: str) -> None:
    """A line in the task's log; the next run of the task reads it."""
    if text.strip():
        store.log(task_id, actor_from_env(), text.strip())


# -- Roles ------------------------------------------------------------------------------------------------------------
def role(store: Store, name: str) -> dict:
    cfg = roles.load(store, name)
    folder = roles.folder(store, name)
    reports = sorted((folder / "reports").glob("*"), key=lambda p: p.stat().st_mtime) \
        if (folder / "reports").is_dir() else []
    keys = ("enabled", "tags", "decides", "escalate_after_minutes", "runs_per_day", "max_turns", "max_minutes", "model")
    return {key: cfg[key] for key in keys} | {
        "name": name, "folder": str(folder), "instructions": str(folder / "CLAUDE.md"),
        "permissions": str(folder / "settings.json"), "report": str(reports[-1]) if reports else None}


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value)  # strings and lists of strings: JSON's syntax is TOML's here


def set_role(store: Store, name: str, **values) -> dict:
    """Change top-level keys of a role's role.toml in place (comments and the rest of the file kept), after checking
    each value. The runner rereads the file before its next task. Returns the role as it now reads."""
    for key, value in values.items():
        if key == "enabled" and not isinstance(value, bool):
            raise ValueError("enabled is true or false")
        if key in LIMITS:
            low, high = LIMITS[key]
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise ValueError(f"{key} is a whole number from {low} to {high}")
        if key == "tags" and (not value or not all(isinstance(t, str) and TAG.match(t) for t in value)):
            raise ValueError("tags: at least one, each lowercase letters, digits and dashes")
        if key == "decides" and not (isinstance(value, list) and all(isinstance(t, str) and TAG.match(t)
                                                                         for t in value)):
            raise ValueError("decides: tags (lowercase letters, digits and dashes), or none")
        if key == "model" and not isinstance(value, str):
            raise ValueError("model is a model name, or empty for Claude Code's default")
        if key not in ("enabled", "tags", "decides", "model", *LIMITS):
            raise ValueError(f"{key} is not a role setting")
    path = roles.folder(store, name) / "role.toml"
    original = path.read_text()
    lines = original.splitlines()
    top = next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    for key, value in values.items():
        pattern = re.compile(rf"^{re.escape(key)}\s*=\s*(\[[^\]]*\]|\"[^\"]*\"|[^#\s]+)(?P<rest>\s*(#.*)?)$")
        for i in range(top):
            match = pattern.match(lines[i])
            if match:
                lines[i] = f"{key} = {_toml(value)}{match.group('rest')}"
                break
        else:
            lines.insert(top, f"{key} = {_toml(value)}")
            top += 1
    text = "\n".join(lines) + "\n"
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"role.toml would not parse: {e}") from None
    path.write_text(text)
    cfg = roles.load(store, name)
    store.register_agent(name, cfg["tags"], cfg["decides"])
    return role(store, name)


# -- Claude Code and tools --------------------------------------------------------------------------------------------
def claude_version() -> str:
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return "not found"
    found = re.findall(r"\d+\.\d+\.\d+", out)
    return found[0] if found else "unknown"


def claude_latest() -> str:
    """The newest Claude Code on npm (a network call: run it off the window's thread)."""
    try:
        out = subprocess.run(["npm", "view", "@anthropic-ai/claude-code", "version"], capture_output=True,
                             text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    return out or "unknown"


def claude_update() -> tuple[bool, str]:
    """`claude update`: (ok, its last line)."""
    try:
        p = subprocess.run(["claude", "update"], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    lines = (p.stdout + p.stderr).strip().splitlines()
    return p.returncode == 0, lines[-1] if lines else ""


def registry(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def tools() -> dict:
    """The agent systems a person can talk to (the chat column's menu) and the MCP servers every agent gets."""
    servers = registry(MCP_SERVERS)
    return {"agents": registry(AGENTS),
            "mcp_servers": {name: " ".join([os.path.basename(s.get("command", ""))] + s.get("args", []))
                            for name, s in sorted(servers.items())}}


def open_file(path: str) -> None:
    """Open a file in the person's editor (instructions, permissions, a report)."""
    opener = shutil.which("xdg-open") or "mousepad"
    subprocess.Popen([opener, path], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
