"""The reviewer: a branch is read by another agent before it lands (role.toml `review = true` on the author's role).

odd1-tasks-land calls `odd1-tasks review TASK` after the author's check passes. The reviewer role (its own folder,
instructions and read-only permissions; never the branch's author) gets the ticket, its log, the diff against main
and the check's output, may read the worktree and run tests, and ends with one verdict line:
    {"verdict": "approve" | "changes" | "escalate", "notes": "..."}
approve: the branch lands (exit 0). changes: the notes go back to the author in its run (exit 6) and the task's log;
the author fixes and lands again. escalate: a person decides (exit 5; the author releases the task blocked with the
notes). Every review is logged on the task (kind "review").
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from . import roles, runner
from .store import Store

ROLE = "reviewer"
MAX_DIFF = 60_000
EXIT = {"approve": 0, "changes": 6, "escalate": 5}


def prompt(task: dict, diff: str, check: str) -> str:
    log = "\n".join(f"- {e['actor']} [{e['kind']}] {e['text'][:300]}" for e in task["log"][-30:])
    cut = f"\n(diff cut at {MAX_DIFF} characters: read the rest with git in the worktree)" if len(diff) > MAX_DIFF else ""
    return f"""You review a branch before it lands on Odd1's main (you are the reviewer; the author is {task['claim_agent']}).

Ticket #{task['id']}: {task['goal']}
Its check (done_when): {task['done_when']}
Its log (latest):
{log}

The author's check output (odd1-tasks-check):
{check[-4000:]}

The diff against main:
{diff[:MAX_DIFF]}{cut}

Review it as your instructions say. End your answer with exactly one line of JSON, nothing after it:
{{"verdict": "approve" | "changes" | "escalate", "notes": "<what to change, or why it is fine, or what a person must decide>"}}
"""


def verdict_of(text: str) -> dict:
    for line in reversed((text or "").strip().splitlines()):
        m = re.search(r"\{.*\}", line)
        if m:
            try:
                v = json.loads(m.group(0))
            except ValueError:
                continue
            if v.get("verdict") in EXIT:
                return {"verdict": v["verdict"], "notes": str(v.get("notes", ""))[:4000]}
    return {"verdict": "escalate", "notes": "the reviewer gave no verdict; a person should look"}


def review(store: Store, task_id: int, worktree: str, check_output: str = "") -> dict:
    task = store.show(task_id)
    if task["claim_agent"] == ROLE:
        raise SystemExit("the reviewer does not review its own work")
    cfg = roles.load(store, ROLE)
    diff = subprocess.run(["git", "diff", "main...HEAD"], cwd=worktree, capture_output=True, text=True).stdout
    cmd = runner.CLAUDE + ["-p", "--output-format", "json", "--max-turns", str(cfg["max_turns"]),
                           "--permission-mode", "dontAsk", "--settings", str(roles.settings(cfg)),
                           "--setting-sources", "project", "--no-session-persistence", "--strict-mcp-config",
                           "--add-dir", str(cfg["folder"]), "--append-system-prompt-file",
                           str(cfg["folder"] / "CLAUDE.md")] + (["--model", cfg["model"]] if cfg["model"] else [])
    env = dict(os.environ, ODD1_TASKS_AGENT=ROLE, ODD1_TASKS_TASK=str(task_id))
    try:
        p = subprocess.run(cmd, cwd=worktree, input=prompt(task, diff, check_output), capture_output=True, text=True,
                           env=env, timeout=cfg["max_minutes"] * 60)
        result = runner._parse(p.stdout)
    except subprocess.TimeoutExpired:
        result = {"result": ""}
    v = verdict_of(result.get("result", ""))
    with store._tx():
        store._log(task_id, ROLE, "review", f"{v['verdict']}: {v['notes']}", verdict=v["verdict"],
                   turns=result.get("num_turns"), cost=result.get("total_cost_usd"))
    return v


def main(args: list[str]) -> int:
    if not args:
        print("usage: odd1-tasks review TASK [CHECK_OUTPUT_FILE]   (in the task's worktree)")
        return 2
    check = open(args[1]).read() if len(args) > 1 and os.path.exists(args[1]) else ""
    v = review(Store(), int(args[0]), os.environ.get("ODD1_TASKS_WORKTREE") or os.getcwd(), check)
    print(f"review: {v['verdict']}\n{v['notes']}")
    return EXIT[v["verdict"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
