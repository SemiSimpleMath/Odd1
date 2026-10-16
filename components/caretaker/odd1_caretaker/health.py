"""The whole system's health at a glance, for the person: the top bar's health icon and its drop-down (popup.py).

summary() gathers, read-only and each with a short timeout, so one broken source never breaks the rest:
  machine  the caretaker's last look (latest.json): memory, load, pressure, disks, failed units; its freshness
  agents   the task store (read-only SQL): who is working on which ticket, runs today; `odd1-tasks attention`:
           what waits for the person and the manager; Claude Code chats open
  backups  /var/backups/odd1: the newest daily (state) and hourly (home) backup
  deploy   `odd1-deploy status --json`: the running commit, the latest deploy, failed (rolled back) ones in 24 h
assess() turns it into ok / warn / bad with the reasons in plain words; panel() is xfce4-genmon-plugin's output.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import status, store

TASKS_DB = Path(os.environ.get("ODD1_TASKS_STATE", "/var/lib/odd1/tasks")) / "tasks.db"
BACKUPS = Path(os.environ.get("ODD1_BACKUP_ROOT", "/var/backups/odd1"))
DAY = 86400
COLOURS = {"ok": "#6FD3A0", "warn": "#E9C27E", "bad": "#E8737C"}
WORDS = {"ok": "All good", "warn": "Needs a look", "bad": "Something is wrong"}


def _run_json(cmd: list[str], timeout: float = 8):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return json.loads(r.stdout) if r.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def machine(directory: Path | None = None) -> dict:
    directory = directory or store.state_dir()
    look = store.read_json(directory / "latest.json") or {}
    mem = look.get("memory") or {}
    total, avail = mem.get("MemTotal"), mem.get("MemAvailable")
    pressure = {k: ((look.get("pressure") or {}).get(k) or {}).get("some", {}).get("avg60")
                for k in ("cpu", "memory", "io")}
    root = (look.get("disks") or {}).get("/") or {}
    return {"fresh": status.freshness(directory).get("state", "unknown"),
            "memory_total": total, "memory_available": avail,
            "memory_used_pct": round(100 * (1 - avail / total), 1) if total and avail is not None else None,
            "cpus": look.get("cpus"), "load": (look.get("load") or [None])[0], "pressure": pressure,
            "disk_used_pct": root.get("used_pct"), "disk_free": root.get("free"),
            "failed_units": look.get("failed_units") or [], "problems": look.get("problems") or []}


def agents(db: Path = TASKS_DB, attention=None, now: float | None = None) -> dict:
    now = now or time.time()
    out = {"working": [], "runs_today": {}, "need_person": 0, "with_manager": 0, "chats": chats()}
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            for tid, agent, goal in con.execute("SELECT id, claim_agent, goal FROM tasks WHERE status = 'claimed' "
                                                "ORDER BY id"):
                started = con.execute("SELECT max(at) FROM log WHERE task_id = ? AND kind = 'claim'", (tid,)).fetchone()[0]
                out["working"].append({"role": agent, "task": tid, "goal": goal,
                                       "minutes": round((now - started) / 60) if started else None})
            out["runs_today"] = dict(con.execute("SELECT actor, count(*) FROM log WHERE kind = 'run' AND at >= ? "
                                                 "GROUP BY actor ORDER BY actor", (midnight,)).fetchall())
        finally:
            con.close()
    except sqlite3.Error as exc:
        out["error"] = f"task store: {exc}"
    items = attention if attention is not None else _run_json(["odd1-tasks", "attention", "--json"])
    if isinstance(items, list):
        out["need_person"] = sum(1 for i in items if i.get("who") == "person")
        out["with_manager"] = sum(1 for i in items if i.get("who") == "manager")
    return out


def chats(proc: Path = Path("/proc")) -> int:
    """Claude Code sessions a person talks to (role agents run `claude -p`, so they are not counted)."""
    n = 0
    for p in proc.glob("[0-9]*/cmdline"):
        try:
            argv = p.read_bytes().split(b"\0")
        except OSError:
            continue
        if argv and os.path.basename(argv[0].decode(errors="replace")) == "claude" and b"-p" not in argv:
            n += 1
    return n


def backups(root: Path = BACKUPS) -> dict:
    def newest(pattern: str, folder: Path, fmt: str):
        stamps = []
        for d in folder.glob("*") if folder.is_dir() else []:
            if re.fullmatch(pattern, d.name):
                try:
                    stamps.append(datetime.strptime(d.name, fmt).timestamp())
                except ValueError:
                    pass
        return max(stamps) if stamps else None
    try:
        return {"daily": newest(r"\d{4}-\d\d-\d\d", root, "%Y-%m-%d"),
                "hourly": newest(r"\d{4}-\d\d-\d\dT\d\d-\d\d", root / "home", "%Y-%m-%dT%H-%M")}
    except OSError as exc:
        return {"daily": None, "hourly": None, "error": str(exc)}


def deploy(data=None, now: float | None = None) -> dict:
    now = now or time.time()
    data = data if data is not None else _run_json(["odd1-deploy", "status", "--json"])
    if not isinstance(data, dict):
        return {"error": "odd1-deploy status gave nothing"}
    history = data.get("history") or []
    last = history[-1] if history else {}
    return {"current": (data.get("current") or "")[:7], "main_ahead_by": data.get("main_ahead_by"),
            "forced": bool(data.get("forced")),
            "last": {k: last.get(k) for k in ("at", "action", "ok", "actor", "reason")} if last else None,
            "failed_24h": sum(1 for h in history if not h.get("ok") and now - (h.get("at") or 0) < DAY)}


def summary(now: float | None = None) -> dict:
    now = now or time.time()
    parts = {"machine": machine, "agents": agents, "backups": backups, "deploy": deploy}
    out = {"at": now}
    for name, gather in parts.items():
        try:
            out[name] = gather()
        except Exception as exc:  # one broken source never hides the others
            out[name] = {"error": f"{type(exc).__name__}: {exc}"}
    out["level"], out["reasons"] = assess(out, now)
    return out


def _gb(n) -> str:
    return f"{n / 1e9:.1f} GB" if n is not None else "?"


def assess(s: dict, now: float | None = None) -> tuple[str, list[str]]:
    """ok / warn / bad, and why, in plain words (worst first). Only what is wrong with the system counts: tickets
    waiting for the person are shown in the drop-down, not as a colour."""
    now = now or time.time()
    bad, warn = [], []
    m, b, d = s.get("machine") or {}, s.get("backups") or {}, s.get("deploy") or {}
    for unit in m.get("failed_units") or []:
        bad.append(f"service failed: {unit}")
    if m.get("fresh") in ("stale", "unhealthy", "unknown"):
        warn.append(f"the caretaker's look is {m.get('fresh')}")
    mem = m.get("memory_used_pct")
    if mem is not None:
        (bad if mem >= 92 else warn if mem >= 85 else []).append(f"memory {mem:.0f}% used")
    disk = m.get("disk_used_pct")
    if disk is not None:
        (bad if disk >= 92 else warn if disk >= 85 else []).append(f"disk {disk:.0f}% full ({_gb(m.get('disk_free'))} free)")
    for kind, value in (m.get("pressure") or {}).items():
        if value is not None and value >= 25:  # a minute's average: a short burst (a build) is not a problem
            (bad if value >= 60 else warn).append(f"{kind} pressure {value:.0f}%")
    for name, limit, worst in (("daily", 1.5 * DAY, 3 * DAY), ("hourly", 3 * 3600, DAY)):
        at = b.get(name)
        if at is None:
            warn.append(f"no {name} backup found")
        elif now - at > worst:
            bad.append(f"last {name} backup {_ago(now - at)} ago")
        elif now - at > limit:
            warn.append(f"last {name} backup {_ago(now - at)} ago")
    last = d.get("last") or {}
    if d.get("forced"):
        warn.append("a forced deploy is running")
    if last and not last.get("ok"):
        warn.append("the last deploy failed and was rolled back")
    for part in ("machine", "agents", "backups", "deploy"):
        if (s.get(part) or {}).get("error"):
            warn.append(f"{part}: could not read ({s[part]['error'][:60]})")
    return ("bad" if bad else "warn" if warn else "ok"), bad + warn


def _ago(seconds: float) -> str:
    seconds = max(0, seconds)
    if seconds < 90:
        return "just now" if seconds < 30 else "1 min"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    if seconds < 2 * DAY:
        return f"{round(seconds / 3600)} h"
    return f"{round(seconds / DAY)} days"


def ago(at, now: float | None = None) -> str:
    if at is None:
        return "never"
    text = _ago((now or time.time()) - at)
    return text if text == "just now" else f"{text} ago"


def icon(level: str, folder: Path | None = None) -> Path:
    """The top bar's icon: a pulse line in the level's colour (22 px, the panel's icon size), drawn once."""
    folder = folder or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "odd1-caretaker"
    path = folder / f"health-{level}-v1.png"
    if not path.exists():
        from PIL import Image, ImageDraw
        scale, size = 8, 22
        img = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
        points = [(1, 12), (6, 12), (8.5, 6), (12, 18), (14.5, 9), (16, 12), (21, 12)]
        colour = COLOURS.get(level, COLOURS["warn"])
        ImageDraw.Draw(img).line([(x * scale, y * scale) for x, y in points], fill=colour, width=int(2.2 * scale),
                                 joint="curve")
        folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        img.resize((size, size), Image.LANCZOS).save(tmp, "PNG")
        tmp.replace(path)
    return path


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def panel(s: dict, icon_path: Path | None = None, click: str = "odd1-caretaker popup") -> str:
    """xfce4-genmon-plugin's output: the icon, the tooltip (the level and its reasons) and the click command."""
    level = s.get("level", "warn")
    tip = WORDS.get(level, level) + "".join(f"\n• {r}" for r in s.get("reasons", [])[:6])
    a = s.get("agents") or {}
    working = ", ".join(f"{w['role']} on #{w['task']}" for w in a.get("working", []))
    tip += f"\nAgents: {working or 'idle'}"
    if a.get("need_person"):
        tip += f"\n{a['need_person']} waiting for you"
    tip += "\nClick for details"
    try:
        shown = f"<img>{icon_path or icon(level)}</img><click>{click}</click>"
    except (ImportError, OSError):  # no Pillow or no cache folder: a coloured dot instead of the drawn icon
        shown = f"<txt><span foreground='{COLOURS.get(level, COLOURS['warn'])}'>●</span></txt><txtclick>{click}</txtclick>"
    return f"{shown}<tool>{_escape(tip)}</tool>"
