"""The caretaker's files under its state directory, written atomically, read by the CLI, MCP server and people.

    health.json    the freshness authority: ok, last_success_at, last_error_at, last_error
    latest.json    the last snapshot (probe.snapshot) ; latest.md the same for people
    history.jsonl  recent snapshots, bounded ; actions.jsonl every repair attempted, with its result
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # not POSIX: tests on other hosts run without the cross-process lock
    fcntl = None

DEFAULT_DIR = "/var/lib/odd1/caretaker"
HISTORY_LINES = 1440  # one day at the default 60 s interval


def state_dir() -> Path:
    return Path(os.environ.get("ODD1_CARETAKER_STATE", DEFAULT_DIR))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write(path: Path, text: str) -> None:
    """Replace a file durably via a unique temporary file in the same directory (never a half-written file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path: Path, data) -> None:
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def append_bounded(path: Path, record: dict, keep: int = HISTORY_LINES) -> None:
    """Append one JSON line; when the file holds twice `keep` lines, keep the newest `keep`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) >= 2 * keep:
        atomic_write(path, "\n".join(lines[-keep:]) + "\n")


def tail_jsonl(path: Path, n: int) -> list:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


@contextmanager
def locked(directory: Path):
    """One writer at a time, across accidental duplicate observers."""
    directory.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        yield
        return
    fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def record_success(directory: Path, snap: dict) -> None:
    write_json(directory / "latest.json", snap)
    atomic_write(directory / "latest.md", report_md(snap))
    append_bounded(directory / "history.jsonl", snap)
    t = now()
    write_json(directory / "health.json", {"ok": True, "updated_at": t, "last_success_at": t,
                                           "last_error_at": None, "last_error": None,
                                           "level": snap.get("level"), "problems": len(snap.get("problems") or [])})


def record_error(directory: Path, exc: BaseException) -> None:
    """A failed look keeps the last good snapshot and its time; it never passes for a fresh one."""
    prior = read_json(directory / "health.json", {}) or {}
    t = now()
    write_json(directory / "health.json", {"ok": False, "updated_at": t, "last_success_at": prior.get("last_success_at"),
                                           "last_error_at": t, "last_error": f"{type(exc).__name__}: {str(exc)[:500]}"})


def report_md(snap: dict) -> str:
    lines = ["# Odd1 caretaker", "", f"Observed {snap.get('time')}. Overall pressure: **{snap.get('level')}**.", ""]
    probs = snap.get("problems") or []
    lines += ["## Needs attention"] + [f"- {p}" for p in probs] if probs else ["Nothing needs attention."]
    fg = snap.get("foreground") or {}
    if fg.get("pid"):
        rss = f"{fg['rss'] // 2**20} MiB" if fg.get("rss") else "?"
        lines += ["", f"Foreground: {fg.get('name')} (pid {fg['pid']}), {rss} resident, pressure {fg.get('level', '?')}."]
    else:
        lines += ["", f"Foreground: unknown ({fg.get('unavailable', 'no data')})."]
    return "\n".join(lines) + "\n"
