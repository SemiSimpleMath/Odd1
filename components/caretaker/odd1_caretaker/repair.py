"""Repair: restart a failed systemd unit and verify that it came back. Every attempt is recorded in actions.jsonl.

The observer repairs on its own only the units it was given (ODD1_CARETAKER_RESTART, from module.nix), and at most
MAX_PER_HOUR times per unit, so a unit that keeps failing is reported rather than restarted forever.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import store

MAX_PER_HOUR = 3
UNIT = re.compile(r"[A-Za-z0-9:_.@\\-]+\.(service|socket|timer|mount|path|target)")


def _systemctl(run, *args, timeout=60):
    prefix = [] if not hasattr(os, "geteuid") or os.geteuid() == 0 else ["sudo", "-n"]
    return run([*prefix, "systemctl", *args], capture_output=True, text=True, timeout=timeout, check=False)


def restart(unit: str, directory: Path | None = None, run=subprocess.run, sleep=time.sleep,
            settle_seconds: int = 10, reason: str = "requested") -> dict:
    """reset-failed + restart, then wait up to settle_seconds for it to be active. Returns what happened."""
    if not UNIT.fullmatch(unit or ""):
        raise ValueError(f"not a systemd unit name: {unit!r}")
    result = {"time": store.now(), "action": "restart", "unit": unit, "reason": reason}
    try:
        _systemctl(run, "reset-failed", unit)
        r = _systemctl(run, "restart", unit)
        if r.returncode != 0:
            result.update(ok=False, error=f"systemctl restart exit {r.returncode}: {(r.stderr or '').strip()[:300]}")
        else:
            state = ""
            for _ in range(max(1, settle_seconds)):
                state = (_systemctl(run, "is-active", unit, timeout=10).stdout or "").strip()
                if state in ("active", "failed"):
                    break
                sleep(1)
            result.update(ok=state == "active", state=state)
            if state != "active":
                result["error"] = f"after restart the unit is {state or 'in an unknown state'}"
    except (OSError, subprocess.SubprocessError) as exc:
        result.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    store.append_bounded((directory or store.state_dir()) / "actions.jsonl", result)
    return result


def recent_restarts(unit: str, directory: Path, within=timedelta(hours=1)) -> int:
    cutoff = datetime.now(timezone.utc) - within
    count = 0
    for a in store.tail_jsonl(directory / "actions.jsonl", 500):
        try:
            when = datetime.fromisoformat(a.get("time", ""))
        except ValueError:
            continue
        if a.get("unit") == unit and a.get("action") == "restart" and when >= cutoff:
            count += 1
    return count


def auto_repair(snap: dict, allowed: list[str], directory: Path, run=subprocess.run, sleep=time.sleep) -> list[dict]:
    """Restart the failed units on the allowed list, within the hourly limit; say plainly when the limit holds one back."""
    done = []
    for unit in snap.get("failed_units") or []:
        if unit not in allowed:
            continue
        if recent_restarts(unit, directory) >= MAX_PER_HOUR:
            snap.setdefault("problems", []).append(
                f"{unit} keeps failing: restarted {MAX_PER_HOUR} times in the last hour, now left for diagnosis")
            continue
        done.append(restart(unit, directory, run, sleep, reason="failed; on the caretaker's restart list"))
    return done
