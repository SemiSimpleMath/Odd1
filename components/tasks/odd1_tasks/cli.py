"""odd1-tasks: the task store for people and scripts (the same operations as the MCP server `tasks`).

    odd1-tasks create GOAL --done-when CMD --tag T [--tag T] [--priority N] [--turns N] [--minutes N]
                      [--depends-on ID] [--resource PATH] [--every 1d] [--attempts N]
    odd1-tasks list [--status S] [--tag T] [--json]
    odd1-tasks show ID [--json]          the task and its whole log
    odd1-tasks approve ID                a person approves a proposed task, or unblocks a blocked one
    odd1-tasks claim [ID] [--as ROLE]    claim ID, or the best ready task the role holds every tag for
    odd1-tasks renew ID                  extend the claim's lease
    odd1-tasks release ID [--blocked REASON]
    odd1-tasks log ID TEXT               append to the task's log
    odd1-tasks finish ID [NOTE]          the claimer is done; the runner then checks done_when

The caller is $ODD1_TASKS_AGENT when the runner set it (an agent), otherwise person:$USER.
State: $ODD1_TASKS_STATE (default /var/lib/odd1/tasks).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from .store import Store, TaskError, actor_from_env


def _when(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"


def _print_task(t: dict) -> None:
    print(f"task {t['id']} [{t['status']}] {t['goal']}")
    for k in ("done_when", "tags", "priority", "resources", "depends_on", "schedule", "parent_id", "created_by",
              "approved_by", "claim_agent"):
        if t.get(k) not in (None, [], ""):
            print(f"  {k}: {', '.join(map(str, t[k])) if isinstance(t[k], list) else t[k]}")
    print(f"  budget: {t['max_turns'] or 'role default'} turns, {t['max_minutes'] or 'role default'} min; "
          f"attempts {t['attempts']}/{t['max_attempts']}" + (f"; lease until {_when(t['lease_until'])}"
                                                               if t["lease_until"] else ""))
    for e in t.get("log", []):
        print(f"  {_when(e['at'])} {e['actor']:<22} {e['kind']:<8} {e['text']}")
        if e["kind"] == "check" and e["data"].get("output", "").strip():
            print("      " + e["data"]["output"].strip()[-400:].replace("\n", "\n      "))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="odd1-tasks", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create")
    p.add_argument("goal")
    p.add_argument("--done-when", required=True)
    p.add_argument("--tag", action="append", default=[])
    p.add_argument("--priority", type=int, default=0)
    p.add_argument("--turns", type=int)
    p.add_argument("--minutes", type=float)
    p.add_argument("--depends-on", type=int, action="append", default=[])
    p.add_argument("--resource", action="append", default=[])
    p.add_argument("--every")
    p.add_argument("--attempts", type=int, default=3)
    p = sub.add_parser("list")
    p.add_argument("--status")
    p.add_argument("--tag")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    for name in ("approve", "renew"):
        sub.add_parser(name).add_argument("id", type=int)
    p = sub.add_parser("claim")
    p.add_argument("id", type=int, nargs="?")
    p.add_argument("--as", dest="agent")
    p = sub.add_parser("release")
    p.add_argument("id", type=int)
    p.add_argument("--blocked", default="")
    p = sub.add_parser("log")
    p.add_argument("id", type=int)
    p.add_argument("text")
    p = sub.add_parser("finish")
    p.add_argument("id", type=int)
    p.add_argument("note", nargs="?", default="")
    a = ap.parse_args(argv)
    actor, store = actor_from_env(), Store()
    try:
        if a.cmd == "create":
            t = store.create(a.goal, a.done_when, a.tag, actor, priority=a.priority, max_turns=a.turns,
                             max_minutes=a.minutes, depends_on=a.depends_on, resources=a.resource,
                             schedule=a.every, max_attempts=a.attempts)
            print(f"task {t['id']} proposed; a person approves it with: odd1-tasks approve {t['id']}")
        elif a.cmd == "list":
            tasks = store.list(a.status, a.tag)
            if a.json:
                print(json.dumps(tasks, indent=2))
            for t in [] if a.json else tasks:
                extra = f" ({t['claim_agent']})" if t["claim_agent"] else ""
                print(f"{t['id']:>4} {t['status']:<9}{extra} [{','.join(t['tags'])}] {t['goal']}")
        elif a.cmd == "show":
            t = store.show(a.id)
            print(json.dumps(t, indent=2)) if a.json else _print_task(t)
        elif a.cmd == "approve":
            t = store.approve(a.id, actor)
            print(f"task {t['id']} {t['status']}")
        elif a.cmd == "claim":
            t = store.claim(a.agent or actor, a.id)
            print(f"task {t['id']} claimed until {_when(t['lease_until'])}" if t else "nothing to claim")
            return 0 if t else 1
        elif a.cmd == "renew":
            print(f"lease until {_when(store.renew(a.id, actor)['lease_until'])}")
        elif a.cmd == "release":
            print(f"task {a.id} {store.release(a.id, actor, a.blocked)['status']}")
        elif a.cmd == "log":
            store.log(a.id, actor, a.text)
        elif a.cmd == "finish":
            print(f"task {a.id} {store.finish(a.id, actor, a.note)['status']}")
    except TaskError as e:
        print(f"odd1-tasks: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
