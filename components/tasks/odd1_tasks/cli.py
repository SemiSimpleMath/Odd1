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
    odd1-tasks decline ID REASON         turn a proposal down (closed; the reason stays in its log)
    odd1-tasks cancel ID REASON          cancel open work (a running agent is stopped at its next renewal)
    odd1-tasks escalate ID REASON        hand a decision to a person
    odd1-tasks attention [--for WHO] [--quiet] [--json]   who has to act on what (exit 0: something waits)
    odd1-tasks changes [--since N] [--json]               what happened after log entry N (the cursor)
    odd1-tasks ask TEXT [--task ID] [--agent SUBAGENT] [--wait MIN]
                                         ask the task manager a question; the answer comes to your conversation
    odd1-tasks inbox [--all] [--json]    the task manager's unanswered messages (--all: with answers)
    odd1-tasks reply ID TEXT             answer one (prints where to send it: SendMessage to that session)
    odd1-tasks watch                     tell the person (desktop notification) what needs them, once each
    odd1-tasks status                    one line: what each role is doing, what waits for a person
    odd1-tasks console                   talk to the task manager (Claude Code with its console instructions)
    odd1-tasks close-by-check ID         a ticket worked in a chat: the task system reruns its check and closes it if it passes
    odd1-tasks close ID REASON           a person closes a ready, blocked or parked ticket as done
    odd1-tasks reopen ID REASON          a person reopens a done ticket
    odd1-tasks recheck [ID]              check tickets closed by check again; flag those failing now
    odd1-tasks regressions BRANCH_BIN MAIN_BIN [COMPONENT...]   every component's tests; refuse new failures
    odd1-tasks review ID [CHECK_OUTPUT]  the reviewer reads the task's branch (from odd1-tasks-land): approve/changes/escalate
    odd1-tasks may-land ID FILE...       may a branch with these changed files land for task ID (decision records)
    odd1-tasks routines [--json]         the cron manager's routines, their schedules and tasks
    odd1-tasks cron [--watch]            sync routines with their recurring tasks (the cron manager; no model calls)

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


def _closing(args: list[str]) -> int:
    from . import closing
    from .store import Store, TaskError, actor_from_env
    store, cmd = Store(), args[0]
    try:
        if cmd == "recheck":
            flagged = closing.recheck(store, int(args[1]) if len(args) > 1 else None)
            print("\n".join(f"#{t['id']} flagged: its check fails now" for t in flagged) or "all checks pass")
            return 0
        if len(args) < 2 or (cmd != "close-by-check" and len(args) < 3):
            print(f"usage: odd1-tasks {cmd} ID" + ("" if cmd == "close-by-check" else " REASON"), file=sys.stderr)
            return 2
        tid = int(args[1])
        if cmd == "close-by-check":
            t = closing.close_by_check(store, tid, actor_from_env())
        elif cmd == "close":
            t = store.close(tid, actor_from_env(), " ".join(args[2:]))
        else:
            t = store.reopen(tid, actor_from_env(), " ".join(args[2:]))
    except TaskError as e:
        print(f"odd1-tasks: {e}", file=sys.stderr)
        return 1
    print(f"task {t['id']} {t['status']}" + ("" if t["status"] != "ready" or cmd != "close-by-check"
                                             else ": its check failed, see odd1-tasks show"))
    return 0 if t["status"] == "done" or cmd == "reopen" else 1


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args[:1] == ["review"]:
        from . import review
        return review.main(args[1:])
    if args[:1] == ["regressions"]:
        from . import regressions
        return regressions.main(args[1:])
    if args[:1] == ["may-land"]:
        from . import landing
        return landing.main(args[1:])
    if args[:1] in (["close-by-check"], ["close"], ["reopen"], ["recheck"]):
        return _closing(args)
    if args[:1] in (["cron"], ["routines"]):
        from . import cron
        return cron.main(args if args[0] == "routines" else args[1:])
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
    p.add_argument("--run-if", help="shell check: run only while it exits 0 (no model call otherwise)")
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
    for name in ("decline", "cancel", "escalate"):
        p = sub.add_parser(name)
        p.add_argument("id", type=int)
        p.add_argument("reason")
    p = sub.add_parser("attention")
    p.add_argument("--for", dest="who")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("changes")
    p.add_argument("--since", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("ask")
    p.add_argument("text")
    p.add_argument("--task", type=int)
    p.add_argument("--agent", help="the subagent asking (its answer goes to its parent session)")
    p.add_argument("--wait", type=float, default=0, metavar="MINUTES", help="wait for the answer (up to 15)")
    p = sub.add_parser("inbox")
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("reply")
    p.add_argument("id", type=int)
    p.add_argument("text")
    sub.add_parser("watch")
    sub.add_parser("status")
    sub.add_parser("console")
    a = ap.parse_args(argv)
    actor, store = actor_from_env(), Store()
    if a.cmd in ("status", "console"):
        from . import console
        if a.cmd == "console":
            return console.run(store)
        print(console.summary(store))
        return 0
    try:
        if a.cmd == "create":
            t = store.create(a.goal, a.done_when, a.tag, actor, priority=a.priority, max_turns=a.turns,
                             max_minutes=a.minutes, depends_on=a.depends_on, resources=a.resource,
                             schedule=a.every, max_attempts=a.attempts, run_if=a.run_if)
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
        elif a.cmd in ("decline", "cancel", "escalate"):
            print(f"task {a.id} {getattr(store, a.cmd)(a.id, actor, a.reason)['status']}")
        elif a.cmd == "attention":
            from . import attention
            found = attention.items(store)
            found = attention.for_whom(found, a.who) if a.who else found
            if a.json:
                print(json.dumps(found, indent=2))
            elif not a.quiet:
                for i in found:
                    print(f"{i['who']:<14} {i['text']}")
            return 0 if found else 1
        elif a.cmd == "changes":
            feed = store.changes(a.since)
            if a.json:
                print(json.dumps(feed, indent=2))
            else:
                for c in feed["changes"]:
                    print(f"{c['id']:>6} {_when(c['at'])} #{c['task_id']} [{c['status']}] {c['actor']} {c['kind']}: "
                          f"{c['text']}")
                print(f"cursor {feed['cursor']}")
        elif a.cmd == "ask":
            from . import inbox
            who = inbox.caller()
            wait = max(0.0, min(a.wait, inbox.MAX_WAIT_MINUTES))
            m = store.ask(who["name"], who["actor"], a.text, session=who["session"], task_id=a.task,
                          agent=a.agent, wait_minutes=wait)
            print(f"message {m['id']} left for the task manager; its answer comes to @{m['sender']}")
            if wait:
                import asyncio
                found = asyncio.run(inbox.wait_answer(store, m["id"], wait))
                print(f"answer: {found['text']}" if found else f"no answer within {wait:g} minutes")
                return 0 if found else 1
        elif a.cmd == "inbox":
            found = store.inbox(a.all)
            if a.json:
                print(json.dumps(found, indent=2))
            for m in [] if a.json else found:
                task = f" (task {m['task_id']})" if m["task_id"] else ""
                print(f"{m['id']:>4} {_when(m['at'])} @{m['sender']}{task}: {m['text']}")
                if m["answer"]:
                    print(f"     answered: {m['answer']}")
        elif a.cmd == "reply":
            from . import inbox
            question, reply = store.reply(a.id, actor, a.text)
            print(json.dumps(inbox.delivery(question, reply), indent=2))
        elif a.cmd == "watch":
            from . import attention
            attention.watch(store)
        elif a.cmd == "finish":
            print(f"task {a.id} {store.finish(a.id, actor, a.note)['status']}")
    except TaskError as e:
        print(f"odd1-tasks: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
