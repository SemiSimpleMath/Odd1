"""Who has to act on what: derived from the store every time it is read, so it is never stale and clears itself as
soon as the task changes. Routine decisions go to a deciding agent (the manager: a role with `decides` tags in its
role.toml); a person gets only what needs one.

A message in the task manager's inbox (inbox.py) needs an answer from the manager (the person's if no manager runs,
or it waited too long). An open task needs a decision when it is proposed (approve or decline), blocked (unblock or cancel), parked (cancel,
or a new task), or ready with no role able to run it (cancel). Its decider is the first running, enabled role whose
`decides` covers the task (store.covers: one tag delegated, none person-only) and that did not propose it;
otherwise the person. It goes to the person
anyway when an agent escalated it (until a person acts on it), or when its decider left it for longer than its
`escalate_after_minutes`. A role's runner that is not running is the person's too.

    odd1-tasks attention [--for person|manager|ROLE] [--quiet] [--json]
    odd1-tasks watch      tell the person (desktop notification) about each new item for them, once
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from . import console, roles
from .store import OPEN, Store, covers

PERSON = "person"
RENOTIFY_S = 86400


def _deciders(store: Store) -> list[dict]:
    out = []
    for name in sorted(p.name for p in (store.state / "roles").iterdir()) if (store.state / "roles").is_dir() else []:
        try:
            cfg = roles.load(store, name)
        except FileNotFoundError:
            continue
        out.append(cfg | {"runner": console._runner(name)})
    return out


def items(store: Store, now: float | None = None) -> list[dict]:
    """Everything someone has to act on: [{key, who, kind, task, text, since}], the person's first."""
    now = time.time() if now is None else now
    cfgs = _deciders(store)
    live = [c for c in cfgs if c["enabled"] and not c["runner"]]
    backlog = {tag for c in cfgs for tag in c["backlog_tags"]}  # worked by people and their chats, not by runners
    marks = {r["task_id"]: r for r in store.db.execute(
        "SELECT task_id, max(CASE WHEN kind = 'escalate' THEN id END) AS esc,"
        " max(CASE WHEN actor LIKE 'person:%' THEN id END) AS person FROM log GROUP BY task_id")}
    last_text = {r["task_id"]: r["text"] for r in store.db.execute(
        "SELECT task_id, text FROM log WHERE id IN (SELECT max(id) FROM log WHERE kind IN ('escalate', 'release')"
        " GROUP BY task_id)")}
    out = []
    for t in store.list():
        if t["status"] not in OPEN + ("parked",):
            continue
        tags = set(t["tags"])
        mark = marks.get(t["id"])
        if mark and mark["esc"] and mark["esc"] > (mark["person"] or 0):
            out.append(_item(t, PERSON, "escalated", last_text.get(t["id"], "escalated"), t["updated_at"]))
            continue
        if t["status"] == "proposed":
            kind, text, since = "decide", "proposed: approve or decline", t["created_at"]
        elif t["status"] == "blocked":
            kind, text, since = "blocked", last_text.get(t["id"], "blocked"), t["updated_at"]
        elif t["status"] == "parked":
            kind, text, since = "parked", "its check kept failing: cancel it, or propose a better task", t["updated_at"]
        elif t["status"] == "ready" and not tags & backlog and not any(tags <= set(c["tags"]) for c in cfgs
                                                                         if c["enabled"]):
            kind, text, since = "orphan", f"approved, but no role takes {', '.join(t['tags'])}", t["updated_at"]
        else:
            continue
        decider = next((c for c in live if covers(c["decides"], tags) and t["created_by"] != c["name"]), None)
        if decider is None:
            out.append(_item(t, PERSON, kind, text, since))
        elif now - since > decider.get("escalate_after_minutes", 120) * 60:
            waited = round((now - since) / 3600, 1)
            out.append(_item(t, PERSON, kind, f"{text} ({decider['name']} has not decided in {waited:g} h)", since))
        else:
            out.append(_item(t, decider["name"], kind, text, since))
    manager = next((c for c in live if "manage" in c["tags"]), None)
    for m in store.inbox():
        text = f"message #{m['id']} from @{m['sender']}: {m['text'][:200]}"
        who = PERSON
        if manager and now - m["at"] <= manager.get("escalate_after_minutes", 120) * 60:
            who = manager["name"]
        elif manager:
            text += f" ({manager['name']} has not answered in {round((now - m['at']) / 3600, 1):g} h)"
        out.append({"key": f"message:{m['id']}", "who": who, "kind": "message", "task": m["task_id"],
                    "text": text, "since": m["at"]})
    flags = store.db.execute(
        "SELECT f.task_id, f.text, f.at FROM log f JOIN tasks t ON t.id = f.task_id WHERE t.status = 'done'"
        " AND f.kind = 'flag' AND f.id = (SELECT max(id) FROM log WHERE task_id = f.task_id AND kind IN"
        " ('flag', 'result', 'reopen', 'close')) AND NOT EXISTS (SELECT 1 FROM log p WHERE p.task_id = f.task_id"
        " AND p.id > f.id AND p.actor LIKE 'person:%')").fetchall()
    for f in flags:  # a ticket closed by its check whose check fails now
        out.append(_item(store.show(f["task_id"]), PERSON, "flagged", f["text"], f["at"]))
    for c in cfgs:
        if c["runner"]:
            out.append({"key": f"runner:{c['name']}", "who": PERSON, "kind": "runner", "task": None,
                        "text": f"the {c['name']} runner is {c['runner']}: its tasks wait", "since": now})
    return sorted(out, key=lambda i: (i["who"] != PERSON, i["since"]))


def _item(t, who, kind, text, since):
    return {"key": f"{kind}:{t['id']}", "who": who, "kind": kind, "task": t["id"],
            "text": f"#{t['id']} {t['goal']}: {text}", "since": since}


def for_whom(all_items: list[dict], who: str) -> list[dict]:
    return [i for i in all_items if i["who"] == who]


# -- Telling the person ---------------------------------------------------------------------------------------------
def notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "Task manager", "-i", "odd1", title, body], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def tell_person(store: Store, now: float | None = None, send=notify) -> list[dict]:
    """Notify the person about their items not told yet (or told over a day ago and still open), in one
    notification. Remembers what it told in <state>/notified.json. Returns the items it told."""
    now = time.time() if now is None else now
    path = store.state / "notified.json"
    try:
        told = json.loads(path.read_text())
    except (OSError, ValueError):
        told = {}
    mine = for_whom(items(store, now), PERSON)
    new = [i for i in mine if now - told.get(i["key"], 0) > RENOTIFY_S]
    if new:
        title = new[0]["text"] if len(new) == 1 else f"{len(new)} things need you"
        body = "Open Agent Settings to decide." if len(new) == 1 else "\n".join(i["text"] for i in new[:5]) + (
            f"\n… and {len(new) - 5} more" if len(new) > 5 else "")
        send(title, body)
    told = {i["key"]: (now if i in new else told[i["key"]]) for i in mine}  # forget what is resolved
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(told))
    os.replace(tmp, path)
    return new


def watch(store: Store, interval: float = 30) -> None:
    while True:
        try:
            tell_person(store)
        except Exception as e:  # keep watching; say what went wrong
            print(f"odd1-tasks watch: {e}", flush=True)
        time.sleep(interval)
