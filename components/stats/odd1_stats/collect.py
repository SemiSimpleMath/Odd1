"""One sample of Odd1's agent and system stats, read-only from other components, appended to samples.jsonl.

Sources: tasks.db (per role and day: runs, outcomes, turns, cost, minutes, budget hits; friction tickets per
component), Claude Code transcripts (read incrementally by byte offset: per session counts only, never text),
the caretaker's history.jsonl (pressure, memory, disks, failed units) and odd1-deploy's deployments.jsonl.
"""
import json
import os
import sqlite3
import time
from pathlib import Path

DAY = 86400
BACKFILL_DAYS = 30


def paths(env=os.environ) -> dict:
    home = Path(env.get("HOME", "/home/agent"))
    return {"state": Path(env.get("ODD1_STATS_STATE", "/var/lib/odd1/stats")),
            "tasks": Path(env.get("ODD1_TASKS_STATE", "/var/lib/odd1/tasks")) / "tasks.db",
            "projects": Path(env.get("ODD1_STATS_PROJECTS", home / ".claude" / "projects")),
            "caretaker": Path(env.get("ODD1_CARETAKER_STATE", "/var/lib/odd1/caretaker")) / "history.jsonl",
            "deploy": Path(env.get("ODD1_DEPLOY_STATE", "/var/lib/odd1/deploy")) / "deployments.jsonl"}


def day_of(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


# -- tasks.db

def tasks_stats(db_path: Path, since: float) -> dict:
    """Per day and role: runs, outcomes, turns, cost, minutes, budget hits; per day and component: friction tickets.
    Days are whole UTC days from `since`'s day on, recomputed each time (the latest sample of a day wins)."""
    if not db_path.exists():
        return {"error": f"no {db_path}"}
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    start = since - since % DAY
    try:
        tasks = {r["id"]: r for r in db.execute("SELECT id, tags, attempts, created_at FROM tasks")}
        rows = db.execute("SELECT task_id, at, actor, kind, text, data FROM log ORDER BY id").fetchall()
    finally:
        db.close()
    roles, friction = {}, {}
    run_role, run_start = {}, {}

    def bucket(t, role):
        r = roles.setdefault(day_of(t), {}).setdefault(role, {})
        return r

    def add(b, key, n=1):
        b[key] = round(b.get(key, 0) + n, 4)

    for r in rows:
        tid, at, actor, kind, text = r["task_id"], r["at"], r["actor"], r["kind"], r["text"] or ""
        if kind == "run":
            run_role[tid], run_start[tid] = actor, at
        role = run_role.get(tid, "(no run)")  # closed by its check or by a person
        if at < start:
            continue
        if kind == "run":
            add(bucket(at, actor), "runs")
        elif kind == "run-end":
            role = actor.split(":", 1)[1] if actor.startswith("runner:") else role
            b = bucket(at, role)
            try:
                data = json.loads(r["data"] or "{}")
            except ValueError:
                data = {}
            add(b, "turns", int(data.get("num_turns") or 0))
            add(b, "cost_usd", float(data.get("total_cost_usd") or 0))
            if tid in run_start:
                add(b, "minutes", round((at - run_start[tid]) / 60, 2))
            if data.get("exit"):
                add(b, "exit_nonzero")
        elif kind == "budget":
            add(bucket(at, actor.split(":", 1)[-1]), "budget_hits")
        elif kind == "result":
            b = bucket(at, role)
            if text.startswith("done"):
                add(b, "done")
                t = tasks.get(tid)
                if t is not None and t["attempts"]:
                    add(b, "done_after_retry")
                if t is not None and "friction" in t["tags"]:
                    for c in _components(t["tags"]):
                        add(friction.setdefault(day_of(at), {}).setdefault(c, {}), "closed")
            elif "parked" in text:
                add(b, "parked")
            elif "retry" in text:
                add(b, "retry")
        elif kind == "release" and text.startswith("blocked"):
            add(bucket(at, actor), "blocked")
    for t in tasks.values():
        if t["created_at"] >= start and "friction" in t["tags"]:
            for c in _components(t["tags"]):
                add(friction.setdefault(day_of(t["created_at"]), {}).setdefault(c, {}), "opened")
    return {"roles": roles, "friction": friction}


def _components(tags_json: str) -> list:
    try:
        tags = json.loads(tags_json)
    except ValueError:
        return ["?"]
    return [t for t in tags if t != "friction"] or ["?"]


# -- Claude Code transcripts

def _tool_server(name: str) -> str:
    return name.split("__")[1] if name.startswith("mcp__") and name.count("__") >= 2 else "builtin"


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def transcript_stats(projects: Path, offsets: dict) -> tuple[dict, dict]:
    """Counts added to each transcript since its stored offset. Returns ({session: counts}, new offsets)."""
    sessions, new = {}, dict(offsets)
    for f in sorted(projects.glob("*/*.jsonl")) if projects.is_dir() else []:
        key = str(f)
        prev = offsets.get(key, {})
        pos, tools = prev.get("pos", 0), dict(prev.get("tools", {}))
        try:
            size = f.stat().st_size
            if size < pos:  # rewritten: start over
                pos, tools = 0, {}
            if size == pos:
                continue
            with open(f, "rb") as fh:
                fh.seek(pos)
                chunk = fh.read()
        except OSError:
            continue
        end = chunk.rfind(b"\n") + 1
        if not end:
            continue
        s = {"project": f.parent.name, "source": "routine" if "odd1-tasks" in f.parent.name else "chat",
             "turns": 0, "tokens_in": 0, "tokens_out": 0, "cache_read": 0, "cache_write": 0, "tool_calls": 0,
             "tool_errors": 0, "denials": 0, "interrupts": 0, "calls_by_tool": {}, "errors_by_tool": {}}
        seen = set(prev.get("last_ids", []))
        last_ids = []
        for line in chunk[:end].splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            msg = e.get("message") if isinstance(e.get("message"), dict) else {}
            if e.get("type") == "assistant":
                mid = msg.get("id")
                if mid not in seen:  # one API message is split over several lines with the same id and usage
                    seen.add(mid)
                    last_ids.append(mid)
                    s["turns"] += 1
                    u = msg.get("usage") or {}
                    s["tokens_in"] += u.get("input_tokens") or 0
                    s["tokens_out"] += u.get("output_tokens") or 0
                    s["cache_read"] += u.get("cache_read_input_tokens") or 0
                    s["cache_write"] += u.get("cache_creation_input_tokens") or 0
                for c in msg.get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        name = c.get("name", "?")
                        tools[c.get("id")] = name
                        s["tool_calls"] += 1
                        s["calls_by_tool"][name] = s["calls_by_tool"].get(name, 0) + 1
            elif e.get("type") == "user":
                content = msg.get("content")
                if isinstance(content, list):
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") == "tool_result" and c.get("is_error"):
                            name = tools.get(c.get("tool_use_id"), "?")
                            text = _text_of(c.get("content"))
                            if "Permission to use" in text or "permission" in text.lower() and "denied" in text:
                                s["denials"] += 1
                            s["tool_errors"] += 1
                            s["errors_by_tool"][name] = s["errors_by_tool"].get(name, 0) + 1
                        elif c.get("type") == "text" and "[Request interrupted" in c.get("text", ""):
                            s["interrupts"] += 1
                elif isinstance(content, str) and "[Request interrupted" in content:
                    s["interrupts"] += 1
        # only the latest tool calls and message ids carry over (a result follows its call closely): offsets stay small
        new[key] = {"pos": pos + end, "tools": dict(list(tools.items())[-20:]),
                    "last_ids": (list(prev.get("last_ids", [])) + last_ids)[-5:]}
        if s["turns"] or s["tool_calls"] or s["interrupts"]:
            sessions[f.stem] = s
    return sessions, new


# -- caretaker and deploy

def caretaker_stats(history: Path) -> dict:
    """The caretaker's latest snapshot, condensed: PSI some avg60 per resource, memory available %, fullest disk,
    failed units."""
    last = _last_jsonl(history)
    if not last:
        return {}
    psi = {k: (v or {}).get("some", {}).get("avg60") for k, v in (last.get("pressure") or {}).items()}
    mem = last.get("memory") or {}
    avail = round(100 * mem["MemAvailable"] / mem["MemTotal"], 1) if mem.get("MemTotal") and mem.get(
        "MemAvailable") is not None else None
    disks = [d.get("used_pct") for d in (last.get("disks") or {}).values() if d.get("used_pct") is not None]
    return {"time": last.get("time"), "psi_avg60": psi, "mem_avail_pct": avail,
            "disk_used_pct_max": max(disks) if disks else None,
            "failed_units": len(last.get("failed_units") or []), "level": last.get("level")}


def _last_jsonl(path: Path):
    try:
        with open(path, "rb") as f:
            f.seek(max(0, f.seek(0, 2) - 65536))
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def deploy_stats(history: Path, seen: int) -> tuple[list, int]:
    """Deploys recorded since the first `seen` lines: action, ok, rollback, time. Returns (new ones, line count)."""
    try:
        lines = [l for l in history.read_text().splitlines() if l.strip()]
    except OSError:
        return [], seen
    if len(lines) < seen:
        seen = 0
    out = []
    for line in lines[seen:]:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        out.append({"at": d.get("at"), "action": d.get("action"), "ok": d.get("ok"),
                    "rollback": "rollback" in str(d.get("reason") or "")})
    return out, len(lines)


# -- one sample

def rotate(state: Path, now: float) -> None:
    """Monthly: samples.jsonl of an earlier month becomes samples-YYYY-MM.jsonl."""
    f = state / "samples.jsonl"
    first = None
    try:
        with open(f) as fh:
            first = json.loads(fh.readline())
    except (OSError, ValueError):
        return
    month = time.strftime("%Y-%m", time.gmtime(first.get("t", now)))
    if month != time.strftime("%Y-%m", time.gmtime(now)):
        f.rename(state / f"samples-{month}.jsonl")


def sample(p: dict | None = None, now: float | None = None) -> dict:
    p = p or paths()
    now = time.time() if now is None else now
    state = p["state"]
    state.mkdir(parents=True, exist_ok=True)
    cursor_file = state / "cursor.json"
    try:
        cursor = json.loads(cursor_file.read_text())
    except (OSError, ValueError):
        cursor = {}
    # days since the last sample (the machine may have been off), the last 30 the first time
    since = max(min(cursor.get("last", 0), now) - DAY, now - BACKFILL_DAYS * DAY)
    out = {"t": round(now, 1), "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}
    try:
        out["tasks"] = tasks_stats(p["tasks"], since)
    except sqlite3.Error as e:
        out["tasks"] = {"error": str(e)}
    out["sessions"], offsets = transcript_stats(p["projects"], cursor.get("transcripts", {}))
    out["system"] = caretaker_stats(p["caretaker"])
    out["deploys"], deploy_seen = deploy_stats(p["deploy"], cursor.get("deploys", 0))
    rotate(state, now)
    with open(state / "samples.jsonl", "a") as f:
        f.write(json.dumps(out, separators=(",", ":")) + "\n")
    tmp = cursor_file.with_suffix(".tmp")
    tmp.write_text(json.dumps({"last": now, "transcripts": offsets, "deploys": deploy_seen}))
    os.replace(tmp, cursor_file)
    return out
