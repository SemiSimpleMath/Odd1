#!/usr/bin/env python3
"""The dojo's material: what the agents' recent runs spent and where they stumbled, as Markdown. Read-only.

    python3 dojo-material.py [--days N]

Per run (from the task store's run-end entries and the transcripts the runner keeps): the role or routine, the task,
turns, cost, outcome, and the stumbles: permission denials (the command refused), failed tool calls (the error),
the same tool call repeated, and the check's result. Grouped by role and routine, worst first.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

STATE = Path(os.environ.get("ODD1_TASKS_STATE", "/var/lib/odd1/tasks"))


def stumbles(path: str | None) -> dict:
    """Failed tool calls and repeated calls in one transcript (Claude Code stream-json)."""
    out = {"errors": [], "repeats": [], "tools": 0}
    if not path or not Path(path).exists():
        return out
    calls, names = Counter(), {}
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        content = (e.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                out["tools"] += 1
                key = f"{c.get('name')} {json.dumps(c.get('input'), sort_keys=True)[:300]}"
                calls[key] += 1
                names[c.get("id")] = key
            elif c.get("type") == "tool_result" and c.get("is_error"):
                text = c.get("content")
                text = " ".join(x.get("text", "") for x in text if isinstance(x, dict)) if isinstance(text, list) else str(text)
                out["errors"].append(f"{names.get(c.get('tool_use_id'), '?')[:160]} -> {text.strip()[:240]}")
    out["repeats"] = [f"{n}x {k[:200]}" for k, n in calls.items() if n >= 3]
    return out


def main(argv=None) -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--days", type=float, default=1)
    args = a.parse_args(argv)
    db = sqlite3.connect(f"file:{STATE / 'tasks.db'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    since = time.time() - args.days * 86400
    tasks = {r["id"]: dict(r) for r in db.execute("SELECT id, goal, tags, status FROM tasks")}
    runs = defaultdict(list)
    for r in db.execute("SELECT task_id, actor, data, at FROM log WHERE kind = 'run-end' AND at >= ? ORDER BY id",
                        (since,)):
        d, t = json.loads(r["data"]), tasks.get(r["task_id"], {})
        tags = json.loads(t.get("tags", "[]"))
        who = next((g for g in tags if g.startswith("routine-")), r["actor"].split(":")[-1])
        checks = [c["data"] for c in db.execute("SELECT data FROM log WHERE task_id = ? AND kind = 'check' AND at >= ?",
                                                 (r["task_id"], r["at"]))]
        runs[who].append({"task": r["task_id"], "goal": t.get("goal", ""), "status": t.get("status"),
                          "turns": d.get("num_turns"), "cost": d.get("total_cost_usd") or 0,
                          "denials": d.get("permission_denials") or [], "check": json.loads(checks[0])["exit"]
                          if checks else None, **stumbles(d.get("transcript"))})
    print(f"# Dojo material: runs of the last {args.days:g} day(s)\n")
    order = sorted(runs, key=lambda w: -sum(len(x["denials"]) + len(x["errors"]) for x in runs[w]))
    for who in order:
        rs = runs[who]
        print(f"## {who}: {len(rs)} runs, {sum(r['turns'] or 0 for r in rs)} turns, ${sum(r['cost'] for r in rs):.2f}")
        for r in rs:
            print(f"- #{r['task']} [{r['status']}] {r['turns']} turns, ${r['cost']:.2f}, check "
                  f"{'passed' if r['check'] == 0 else 'failed' if r['check'] is not None else 'not run'}: {r['goal'][:120]}")
            for d in r["denials"][:8]:
                print(f"  - DENIED {d.get('tool_name')}: {json.dumps(d.get('tool_input'))[:200]}")
            for e in r["errors"][:8]:
                print(f"  - ERROR {e}")
            for rep in r["repeats"][:5]:
                print(f"  - REPEATED {rep}")
        print()


if __name__ == "__main__":
    main()
