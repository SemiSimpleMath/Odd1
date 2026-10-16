"""Closing tickets worked outside the runners (a person's chat), and rechecking them.

A chat cannot claim a ticket (the person's rule), so nothing could mark its work done. Now:
- close by check: the task system itself reruns the ticket's done_when where a runner would (the folder and the
  environment of the role that holds the ticket's tags, read from that role's runner unit); it closes the ticket only
  if the check passes. Only an approved, ready ticket qualifies; its done_when cannot change after approval (the
  store's fixed_after_approval rule), so the approved check is the one that runs. The log says closed by check and
  where it came from; no claimant, no attempt used.
- recheck: tickets closed by check are checked again (the cron manager, hourly); one whose check fails now is
  flagged (a log entry, and attention.py gives it to the person).
- a person may close a ticket themselves, or reopen a done one, each with a reason (store.close, store.reopen).

    odd1-tasks close-by-check ID       odd1-tasks close ID REASON      odd1-tasks reopen ID REASON
    odd1-tasks recheck [ID]
"""
from __future__ import annotations

import os
import shlex
import subprocess

from . import inbox, roles, runner
from .store import Store, TaskError, run_check


def unit_env(role: str) -> dict | None:
    """The environment systemd gives the role's runner (None when there is no such unit)."""
    try:
        out = subprocess.run(["systemctl", "show", "-p", "Environment", "--value", f"odd1-tasks-runner@{role}"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not out:
        return None
    env = dict(w.split("=", 1) for w in shlex.split(out) if "=" in w)
    env.setdefault("PATH", os.environ.get("PATH", ""))
    return env


def where_to_check(store: Store, task: dict) -> tuple[str | None, object, dict]:
    """(role, folder, environment) a runner would check the task in: the first role holding every tag of it."""
    for name in sorted(p.name for p in (store.state / "roles").iterdir()) if (store.state / "roles").is_dir() else []:
        try:
            cfg = roles.load(store, name)
        except FileNotFoundError:
            continue
        if set(task["tags"]) <= set(cfg["tags"]):
            return name, runner.task_folder(store, cfg, task), unit_env(name) or dict(os.environ)
    return None, store.state, dict(os.environ)


def source() -> str:
    """Where a close was asked from: the Claude Code session (its name), else the caller's user."""
    me = inbox.own_session()
    if me:
        return f"chat {me.get('name') or me.get('id')}"
    return os.environ.get("USER", "unknown")


def close_by_check(store: Store, task_id: int, actor: str, asked_from: str | None = None) -> dict:
    if os.environ.get("ODD1_TASKS_AGENT"):
        raise TaskError("a role agent finishes its task (tasks_finish); close by check is for work done outside runs")
    task = store.show(task_id)
    if task["status"] != "ready" or not task["approved_at"]:
        raise TaskError(f"task {task_id} is {task['status']}: only an approved, ready task closes by its check")
    role, folder, env = where_to_check(store, task)
    code, out = run_check(task, folder, env)
    where = f"as the {role} runner would, in {folder}" if role else f"no role holds its tags: in {folder}"
    return store.close_checked(task_id, actor, asked_from or source(), where, code, out)


def recheck(store: Store, task_id: int | None = None) -> list[dict]:
    """Check tickets closed by check again; flag each whose check fails now (once per failure). Returns those
    flagged."""
    flagged = []
    for t in [store.show(i) for i in ([task_id] if task_id else [t["id"] for t in store.list(status="done")])]:
        log = t["log"]
        closed = [e for e in log if e["kind"] == "result" and "closed by check" in e["text"]]
        if t["status"] != "done" or not closed or any(e["kind"] == "reopen" and e["id"] > closed[-1]["id"]
                                                        for e in log):
            continue
        role, folder, env = where_to_check(store, t)
        code, out = run_check(t, folder, env)
        if code != 0 and not any(e["kind"] == "flag" and e["id"] > closed[-1]["id"] for e in log):
            store.log(t["id"], "tasks:recheck", f"its check fails now (exit {code}): reopen it, or leave it done if "
                      "that was meant", kind="flag", exit=code, output=out)
            flagged.append(store.show(t["id"]))
    return flagged
