"""odd1-tasks-runner@<role>: hands ready tasks for one role to Claude Code, one at a time.

Each loop (no model calls): read role.toml, expire leases and start due recurring runs, check any task the role left
in verifying, and, if the role has runs left today, claim a ready task it holds every tag for. Then start
`claude -p` in the role's folder with the role's settings (dontAsk: anything not allowed is refused), only the
tasks MCP server, and the task's goal, check and log on stdin; renew the lease every 2 min; stop it at the turn
budget (--max-turns) or the time budget (kill); run done_when; record the result.

    python -m odd1_tasks.runner ROLE [--once]
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import cron, roles, workspace
from .store import CLOSED, Store, TaskError

RENEW_SECONDS = float(os.environ.get("ODD1_TASKS_RENEW_SECONDS", 120))
POLL_SECONDS = float(os.environ.get("ODD1_TASKS_POLL_SECONDS", 30))
CLAUDE = shlex.split(os.environ.get("ODD1_TASKS_CLAUDE", "claude"))


def say(role: str, text: str) -> None:
    print(f"odd1-tasks-runner@{role}: {text}", flush=True)


REPLIES_S = 86400  # answers from the task manager shown in a role's prompt: those of the last day


def prompt(task: dict, role: str, turns: int, minutes: float, workdir=None, replies=(), routine=None) -> str:
    log = "\n".join(f"- {datetime.fromtimestamp(e['at']):%Y-%m-%d %H:%M} {e['actor']} [{e['kind']}] {e['text']}"
                    for e in task["log"])
    answers = "".join(f"\n- #{r['reply_to']} you asked \u201c{r['question']}\u201d; the task manager: {r['text']}"
                      for r in replies)
    return f"""You are the {role} agent of Odd1, working on task {task['id']}.

Goal: {task['goal']}
Done when this command exits 0 (the runner runs it after you stop, in this folder; your word does not count):
    {task['done_when']}
Resources this task may change: {', '.join(task['resources']) or '(none named)'}
Budget: {turns} turns, {minutes:g} minutes. Attempt {task['attempts'] + 1} of {task['max_attempts']}.
{f"Your workspace: {workdir} (git branch {workspace.branch(task['id'])}, made from main; your own, nobody else's)."
 if workdir else ""}
{f"This is the routine `{routine['name']}` of the cron manager. Use the `{routine['name']}` skill: it says how. You "
 f"work in its folder, {routine['folder']}." if routine else ""}
The task's log so far (an earlier run may have been interrupted: resume from it, do not repeat finished steps):
{log}

{f"Answers from the task manager to your questions (tasks_ask):{answers}" if answers else ""}
Record each step that matters with tasks_log. Ask the task manager a question with tasks_ask. When the goal is met, call tasks_finish. If you cannot go on without a
person, call tasks_release with the reason in `blocked`. Anything outside your role becomes a proposal (tasks_create).
"""


def command(cfg: dict, task_id: int, turns: int, workdir=None, settings=None) -> list[str]:
    mcp = {"mcpServers": {"tasks": {"command": sys.executable, "args": ["-m", "odd1_tasks.mcp_server"], "env": {
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""), "ODD1_TASKS_STATE": os.environ.get("ODD1_TASKS_STATE", ""),
        "ODD1_TASKS_AGENT": cfg["name"], "ODD1_TASKS_TASK": str(task_id)}}}}
    cmd = CLAUDE + ["-p", "--output-format", "json", "--max-turns", str(turns), "--permission-mode", "dontAsk",
                    "--settings", str(settings or cfg["folder"] / "settings.json"), "--setting-sources", "project",
                    "--mcp-config", json.dumps(mcp), "--strict-mcp-config", "--no-session-persistence"]
    if workdir:  # the repository's own CLAUDE.md applies there; the role's instructions come along
        cmd += ["--add-dir", str(cfg["folder"]), "--append-system-prompt-file", str(cfg["folder"] / "CLAUDE.md")]
    return cmd + (["--model", cfg["model"]] if cfg["model"] else [])


def routine_run(cfg: dict, routine: dict) -> Path:
    """Hand a worker a routine: the routine's SKILL.md as a skill of its folder (where the agent starts), and the
    worker's permissions plus the routine's `allow`. Returns the settings file for the run."""
    folder, name = routine["folder"], routine["name"]
    skill = folder / ".claude" / "skills" / name / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text((folder / "SKILL.md").read_text())
    settings = json.loads((cfg["folder"] / "settings.json").read_text())
    allow = settings.setdefault("permissions", {}).setdefault("allow", [])
    allow += [rule for rule in routine["allow"] if rule not in allow]
    path = cfg["folder"] / "routine-settings" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=1))
    return path


def task_folder(store: Store, cfg: dict, task: dict) -> Path:
    """Where a task's agent starts and its done_when runs: its routine's folder, else the role's."""
    routine = cron.routine_of(store, task) if cfg["all_routines"] else None
    return routine["folder"] if routine else cfg["folder"]


def _stop(proc: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
            proc.wait(timeout=10)
            return
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            continue


def run_task(store: Store, cfg: dict, task: dict) -> dict:
    role, tid = cfg["name"], task["id"]
    turns = min(task["max_turns"] or cfg["max_turns"], cfg["max_turns"])
    minutes = min(task["max_minutes"] or cfg["max_minutes"], cfg["max_minutes"])
    store.log(tid, role, f"run started (budget {turns} turns, {minutes:g} min)", kind="run", turns=turns,
              minutes=minutes)
    say(role, f"task {tid}: {task['goal']}")
    workdir = workspace.prepare(store.state, tid) if cfg["workspace"] == "git-worktree" else None
    routine = cron.routine_of(store, task) if cfg["all_routines"] else None
    settings = routine_run(cfg, routine) if routine else None
    env = dict(os.environ, **{k: str(v) for k, v in cfg["env"].items()}, ODD1_TASKS_AGENT=role,
               ODD1_TASKS_TASK=str(tid), ODD1_TASKS_ROLE_FOLDER=str(cfg["folder"]))
    if workdir:
        env["ODD1_TASKS_WORKTREE"] = str(workdir)
    cmd = command(cfg, tid, turns, workdir, settings)
    if routine:  # the worker's own instructions come along; the routine's folder has the skill
        cmd += ["--append-system-prompt-file", str(cfg["folder"] / "CLAUDE.md")]
    proc = subprocess.Popen(cmd, cwd=workdir or (routine["folder"] if routine else cfg["folder"]), env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    proc.stdin.write(prompt(store.show(tid), role, turns, minutes, workdir,
                            store.replies(role, time.time() - REPLIES_S), routine))
    proc.stdin.close()
    out: list[str] = []
    reader = _drain(proc, out)
    deadline, stopped = time.monotonic() + minutes * 60, ""
    while proc.poll() is None:
        try:
            proc.wait(timeout=max(0.1, min(RENEW_SECONDS, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
        if proc.poll() is not None:
            break
        if time.monotonic() >= deadline:
            stopped = f"time budget reached ({minutes:g} min): stopped"
        else:
            try:
                store.renew(tid, f"runner:{role}")
                continue
            except TaskError as e:
                stopped = f"lost the claim ({e}): stopped"
        _stop(proc)
        break
    reader.join()
    result = _parse("".join(out))
    if stopped:
        store.log(tid, f"runner:{role}", stopped, kind="budget")
    elif result.get("subtype") == "error_max_turns":
        store.log(tid, f"runner:{role}", f"turn budget reached ({turns} turns): stopped", kind="budget")
    store.log(tid, f"runner:{role}", f"run ended: exit {proc.returncode}, {result.get('num_turns', '?')} turns, "
              f"${result.get('total_cost_usd', 0):.2f}", kind="run-end", exit=proc.returncode,
              **{k: result[k] for k in ("subtype", "num_turns", "total_cost_usd", "result", "permission_denials") if k in result})
    task = store.show(tid)
    if task["status"] == "claimed" and task["claim_agent"] == role:
        task = store.finish(tid, f"runner:{role}", "run ended without tasks_finish; checking done_when anyway")
    if task["status"] == "verifying":
        task = store.verify(tid, f"runner:{role}", cwd=routine["folder"] if routine else cfg["folder"])
    if workdir and task["status"] in CLOSED:
        workspace.remove(store.state, tid)
    say(role, f"task {tid}: {task['status']}")
    return task


def _drain(proc, out):
    t = threading.Thread(target=lambda: out.append(proc.stdout.read()), daemon=True)
    t.start()
    threading.Thread(target=proc.stderr.read, daemon=True).start()
    return t


def _parse(text: str) -> dict:
    for line in reversed(text.strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            if isinstance(data.get("result"), str):
                data["result"] = data["result"][-2000:]
            return data
    return {"result": text[-2000:]} if text.strip() else {}


def run_once(store: Store, role: str) -> dict | None:
    """One loop: check leftovers, then claim and run at most one task. Returns the task run, if any."""
    cfg = roles.load(store, role)
    store.register_agent(role, cfg["tags"], cfg["decides"])
    store.maintain()
    for t in store.list(status="verifying"):
        if t["claim_agent"] == role:
            store.verify(t["id"], f"runner:{role}", cwd=task_folder(store, cfg, t))
    if cfg["workspace"] == "git-worktree":
        workspace.tidy(store)
    if not cfg["enabled"] or store.runs_since(role, time.time() - 86400) >= cfg["runs_per_day"]:
        return None  # paused by the person (role.toml enabled = false), or today's runs are used up
    task = store.claim(role)
    return run_task(store, cfg, task) if task else None


def outdated(role: str) -> bool:
    """Whether a deployment changed this runner's unit since it started (its PYTHONPATH names the code). A switch does
    not restart runners (a fixer may be the one switching); each restarts itself once it is idle."""
    if not os.environ.get("INVOCATION_ID"):
        return False  # not under systemd
    try:
        unit = subprocess.run(["systemctl", "show", "-p", "Environment", "--value", f"odd1-tasks-runner@{role}"],
                              capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    deployed = next((w.split("=", 1)[1] for w in unit.split() if w.startswith("PYTHONPATH=")), "")
    return bool(deployed) and deployed != os.environ.get("PYTHONPATH", "")


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__)
        return 2
    role, store = args[0], Store()
    roles.seed(store, role)
    say(role, f"started; folder {roles.folder(store, role)}")
    while True:
        try:
            task = run_once(store, role)
        except (TaskError, FileNotFoundError) as e:
            say(role, f"error: {e}")
            task = None
        if "--once" in args:
            return 0
        if task is None and outdated(role):
            say(role, "new code deployed: restarting while idle")
            return 0  # systemd starts the runner again, with the new unit
        if task is None:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
