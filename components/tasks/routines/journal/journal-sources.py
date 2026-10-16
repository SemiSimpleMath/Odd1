#!/usr/bin/env python3
"""The day's material for the journal, as Markdown on stdout: what the person said in their chats (each message with
the end of what it answered), every decision in the task store (approve, decline, cancel, escalate, blocked, results,
the person's notes), tickets opened, commits on main, and deploys. Read-only.

    python3 journal-sources.py [--since ISO-TIME] [--until ISO-TIME]

--since defaults to when the newest journal/*.md was written (where the last entry stopped), else 24 hours ago.
Sources: $ODD1_TASKS_STATE/tasks.db, ~/.claude/projects/*/*.jsonl (the person's conversations; agents' role and
scratch folders skipped), git log of $ODD1_REPO main, $ODD1_DEPLOY_STATE/deployments.jsonl.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

STATE = Path(os.environ.get("ODD1_TASKS_STATE", "/var/lib/odd1/tasks"))
REPO = Path(os.environ.get("ODD1_REPO", "/srv/odd1"))
DEPLOY = Path(os.environ.get("ODD1_DEPLOY_STATE", "/var/lib/odd1/deploy"))
PROJECTS = Path(os.environ.get("ODD1_JOURNAL_PROJECTS", Path.home() / ".claude" / "projects"))
JOURNAL = Path(os.environ.get("ODD1_JOURNAL_DIR", "journal"))
DECISIONS = ("approve", "decline", "cancel", "escalate", "release", "result", "note")
SAID, ANSWERED, LIMIT = 1200, 500, 150_000  # characters per message, of the answer before it, in all


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def since_default(now: datetime) -> datetime:
    entries = sorted(JOURNAL.glob("*.md"), key=lambda p: p.stat().st_mtime)
    return datetime.fromtimestamp(entries[-1].stat().st_mtime) if entries else now - timedelta(hours=24)


def tasks(since: float, until: float) -> list[str]:
    path = STATE / "tasks.db"
    if not path.exists():
        return ["(no task store)"]
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    goals = {r["id"]: r["goal"] for r in db.execute("SELECT id, goal FROM tasks")}
    out = ["### Tickets opened"]
    for r in db.execute("SELECT id, goal, tags, created_by, status, created_at FROM tasks "
                        "WHERE created_at >= ? AND created_at < ? ORDER BY id", (since, until)):
        out.append(f"- #{r['id']} [{r['status']}] by {r['created_by']} at {_when(r['created_at'])} "
                   f"({', '.join(json.loads(r['tags']))}): {r['goal']}")
    out.append("\n### Decisions and results (task log)")
    marks = ",".join("?" * len(DECISIONS))
    for r in db.execute(f"SELECT task_id, at, actor, kind, text FROM log WHERE at >= ? AND at < ? AND kind IN ({marks})"
                        " ORDER BY id", (since, until, *DECISIONS)):
        if r["kind"] == "note" and not r["actor"].startswith("person:"):
            continue  # agents' working notes are not decisions
        if r["kind"] == "release" and "blocked" not in r["text"]:
            continue
        goal = goals.get(r["task_id"], "")
        out.append(f"- {_when(r['at'])} #{r['task_id']} {r['kind']} by {r['actor']}: {r['text'][:400]}"
                   f" — ({goal[:160]})")
    return out


def commits(since: float, until: float) -> list[str]:
    p = subprocess.run(["git", "-C", str(REPO), "log", "main", f"--since=@{int(since)}", f"--until=@{int(until)}",
                        "--reverse", "--format=- %h %ad %s", "--date=format:%m-%d %H:%M"],
                       capture_output=True, text=True)
    return p.stdout.splitlines() or ["(none)"]


def deploys(since: float, until: float) -> list[str]:
    try:
        lines = (DEPLOY / "deployments.jsonl").read_text().splitlines()
    except OSError:
        return ["(none)"]
    out = []
    for d in map(json.loads, filter(None, lines)):
        if since <= d["at"] < until:
            out.append(f"- {_when(d['at'])} {d['commit'][:12]} {d['action']} {'ok' if d['ok'] else 'FAILED'} "
                       f"by {d['actor']} {d.get('reason') or ''}".rstrip())
    return out or ["(none)"]


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _person_wrote(entry: dict) -> str | None:
    """The person's own words in a transcript line: typed text, not tool results, reminders or commands."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("toolUseResult") is not None:
        return None
    content = entry.get("message", {}).get("content")
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not text or text.startswith("<") or text.startswith("Caveat:"):
        return None
    return text


def conversations(since: datetime, until: datetime) -> list[str]:
    out = []
    folders = [p for p in PROJECTS.iterdir() if p.is_dir()] if PROJECTS.is_dir() else []
    for folder in sorted(folders):
        if folder.name.startswith("-tmp") or "-tasks-roles-" in folder.name:
            continue  # scratch folders and the roles' own runs
        for path in sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
            if datetime.fromtimestamp(path.stat().st_mtime) < since:
                continue
            said, last_answer, title = [], "", ""
            for line in path.open(errors="replace"):
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") == "ai-title":
                    title = e.get("aiTitle", "")
                if e.get("type") == "assistant":
                    text = _text(e.get("message", {}).get("content"))
                    if text.strip():
                        last_answer = text.strip()
                    continue
                text = _person_wrote(e)
                stamp = e.get("timestamp")
                if text is None or not stamp:
                    continue
                at = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
                if since <= at < until:
                    before = last_answer[-ANSWERED:].replace("\n", " ")
                    said.append(f"- {at:%m-%d %H:%M} answering “…{before}”\n  **person:** {text[:SAID]}"
                                if before else f"- {at:%m-%d %H:%M} **person:** {text[:SAID]}")
            if said:
                out.append(f"### {folder.name} / {title or path.stem[:8]}")
                out.extend(said)
    return out or ["(none)"]


def main(argv=None) -> None:
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("--since")
    a.add_argument("--until")
    args = a.parse_args(argv)
    now = datetime.now()
    until = datetime.fromisoformat(args.until) if args.until else now
    since = datetime.fromisoformat(args.since) if args.since else since_default(now)
    s, u = since.timestamp(), until.timestamp()
    parts = [f"# Journal sources {since:%Y-%m-%d %H:%M} to {until:%Y-%m-%d %H:%M}",
             "\n## Task manager", *tasks(s, u),
             "\n## Commits on main", *commits(s, u),
             "\n## Deploys", *deploys(s, u),
             "\n## The person's conversations", *conversations(since, until)]
    text = "\n".join(parts)
    if len(text) > LIMIT:
        text = text[:LIMIT] + f"\n\n(cut at {LIMIT} characters: run again with a later --since for the rest)"
    print(text)


if __name__ == "__main__":
    main()
