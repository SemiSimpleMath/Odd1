"""The cron manager: pre-planned routines, each run on its schedule by whichever worker agent is free, handed the
routine's skill.

A routine is a folder, <state>/routines/<name>/ (seeded from the repository's top-level routines/ like role folders: a file the
person edited stays theirs; a folder the person adds is a routine too):
    routine.toml   enabled, schedule ("1d"), goal, done_when (run in this folder), max_turns, max_minutes,
                   allow (permission rules the run needs on top of the worker role's)
    SKILL.md       the skill: how to do it (a Claude Code skill; its name is the routine's)
    ...            helpers the skill uses
The cron manager (no model calls) keeps one recurring task per enabled routine, tagged `routine` and
`routine-<name>`, created as a proposal (a person, or the manager when `routine` is in its decides, approves it
once); the store then starts a run on the schedule. A routine switched off or removed has its series cancelled; one
whose goal, check or schedule changed gets a new series. A worker role (role.toml all_routines = true) claims the
runs; its runner starts the agent in the routine's folder with the skill and the routine's permissions
(runner.routine_run).

    odd1-tasks cron [--watch]      sync routines with their tasks (once, or every minute)
    odd1-tasks routines            each routine, its schedule and its task
"""
from __future__ import annotations

import json
import os
import time
import tomllib
from pathlib import Path

from .roles import copy_missing
from .store import OPEN, Store

ACTOR = "cron"
# The routines are content (a schedule, a skill, helpers), at the repository's top level like the wiki.
SEED = Path(os.environ.get("ODD1_TASKS_ROUTINES_SEED", Path(__file__).resolve().parents[3] / "routines"))
DEFAULTS = {"enabled": True, "schedule": "1d", "max_turns": None, "max_minutes": None, "allow": []}


def folder(store: Store) -> Path:
    return store.state / "routines"


def tag(name: str) -> str:
    return f"routine-{name}"


def names(store: Store) -> list[str]:
    where = folder(store)
    return sorted(p.name for p in where.iterdir() if (p / "routine.toml").exists()) if where.is_dir() else []


def load(store: Store, name: str) -> dict:
    path = folder(store) / name / "routine.toml"
    cfg = dict(DEFAULTS, **tomllib.loads(path.read_text()))
    for key in ("goal", "done_when"):
        if not str(cfg.get(key, "")).strip():
            raise ValueError(f"routine {name}: {key} is missing")
    return cfg | {"name": name, "folder": path.parent}


def seed(store: Store, source: Path = SEED) -> None:
    for src in sorted(p for p in source.iterdir() if p.is_dir()) if source.is_dir() else []:
        copy_missing(src, folder(store) / src.name)


def series(store: Store, name: str) -> list[dict]:
    """The routine's open series: recurring tasks the cron manager made for it that are not ended."""
    return [t for t in store.list() if t["created_by"] == ACTOR and t["parent_id"] is None
            and tag(name) in t["tags"] and t["status"] not in ("cancelled", "declined")]


def _end(store: Store, t: dict, reason: str) -> None:
    if t["status"] == "proposed":
        store.decline(t["id"], ACTOR, reason)
    else:
        store.cancel(t["id"], ACTOR, reason)


def sync(store: Store, source: Path = SEED) -> list[str]:
    """Bring the routines' tasks in line with the routine folders. Returns what it did."""
    seed(store, source)
    did, known = [], set()
    for name in names(store):
        known.add(name)
        try:
            r = load(store, name)
        except (ValueError, tomllib.TOMLDecodeError) as e:
            did.append(f"{name}: not loaded ({e})")
            continue
        live = series(store, name)
        if not r["enabled"]:
            for t in live:
                _end(store, t, f"routine {name} switched off")
                did.append(f"{name}: switched off, ended #{t['id']}")
            continue
        want = (r["goal"], r["done_when"], r["schedule"])
        for t in live:
            if (t["goal"], t["done_when"], t["schedule"]) != want:
                _end(store, t, f"routine {name} changed: a new series replaces it")
                did.append(f"{name}: changed, ended #{t['id']}")
        if not series(store, name):
            t = store.create(r["goal"], r["done_when"], ["routine", tag(name)], actor=ACTOR, schedule=r["schedule"],
                             max_turns=r["max_turns"], max_minutes=r["max_minutes"])
            did.append(f"{name}: proposed #{t['id']} (every {r['schedule']})")
    for t in store.list():  # routines whose folder is gone
        name = next((g[len("routine-"):] for g in t["tags"] if g.startswith("routine-")), None)
        if (t["created_by"] == ACTOR and t["parent_id"] is None and name and name not in known
                and t["status"] not in ("cancelled", "declined")):
            _end(store, t, f"routine {name} was removed")
            did.append(f"{name}: removed, ended #{t['id']}")
    return did


def listing(store: Store) -> list[dict]:
    out = []
    for name in names(store):
        try:
            r = load(store, name)
        except (ValueError, tomllib.TOMLDecodeError) as e:
            out.append({"name": name, "error": str(e)})
            continue
        live = series(store, name)
        out.append({"name": name, "enabled": r["enabled"], "schedule": r["schedule"], "goal": r["goal"],
                    "task": live[0]["id"] if live else None, "status": live[0]["status"] if live else None,
                    "folder": str(r["folder"])})
    return out


def routine_of(store: Store, task: dict) -> dict | None:
    """The routine a task runs, from its routine-<name> tag, if that routine's folder exists."""
    for g in task["tags"]:
        if g.startswith("routine-") and (folder(store) / g[len("routine-"):] / "routine.toml").exists():
            return load(store, g[len("routine-"):])
    return None


RECHECK_S = 3600  # tickets closed by check are checked again this often (closing.recheck)


def watch(store: Store, interval: float = 60) -> None:
    from . import closing
    rechecked = 0.0
    while True:
        try:
            for line in sync(store):
                print(f"odd1-tasks cron: {line}", flush=True)
            if time.monotonic() - rechecked > RECHECK_S:
                rechecked = time.monotonic()
                for t in closing.recheck(store):
                    print(f"odd1-tasks cron: #{t['id']} flagged: its check fails now", flush=True)
        except Exception as e:  # keep going; say what went wrong
            print(f"odd1-tasks cron: {e}", flush=True)
        time.sleep(interval)


def main(args: list[str]) -> int:
    store = Store()
    if args[:1] == ["routines"]:
        rows = listing(store)
        if "--json" in args:
            print(json.dumps(rows, indent=2))
        for r in rows if "--json" not in args else []:
            print(f"{r['name']:12} " + (r["error"] if "error" in r else
                  f"{'on ' if r['enabled'] else 'off'} every {r['schedule']:4} "
                  + (f"#{r['task']} {r['status']}" if r["task"] else "no task") + f"  {r['goal']}"))
        return 0
    if "--watch" in args:
        watch(store)
    for line in sync(store) or ["nothing to do"]:
        print(line)
    return 0
