"""Is the caretaker's report current? Freshness from health.json, separate from whether its service is running.

States: healthy (recent successful look), stale (last success too old), unhealthy (latest look failed),
unknown (no usable health record). A failed look never counts as fresh, and the last good report's age is always
shown so it cannot be mistaken for a new one.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import store

UNIT = "odd1-caretaker.service"


def freshness(directory: Path, max_age_seconds: int = 180, now: datetime | None = None) -> dict:
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    current = now or datetime.now(timezone.utc)
    info = {}
    latest = directory / "latest.json"
    if latest.is_file():
        info["report_age_seconds"] = max(0, int(current.timestamp() - latest.stat().st_mtime))
    health = store.read_json(directory / "health.json")
    if health is None:
        return {"state": "unknown", "reason": "health.json missing or unreadable", **info}
    if not isinstance(health, dict) or not isinstance(health.get("ok"), bool):
        return {"state": "unknown", "reason": "health.json has no boolean ok", **info}
    if not health["ok"]:
        return {"state": "unhealthy", "reason": "latest look failed", "last_error_at": health.get("last_error_at"),
                "last_error": health.get("last_error"), "last_success_at": health.get("last_success_at"), **info}
    try:
        stamp = datetime.fromisoformat(str(health.get("last_success_at")).replace("Z", "+00:00"))
    except ValueError:
        return {"state": "unknown", "reason": "last_success_at is not a timestamp", **info}
    if stamp.tzinfo is None:
        return {"state": "unknown", "reason": "last_success_at has no timezone", **info}
    age = (current - stamp).total_seconds()
    if age < -60:
        return {"state": "unknown", "reason": "last_success_at is in the future", **info}
    if "report_age_seconds" not in info:
        return {"state": "unknown", "reason": "latest.json missing", **info}
    state = "stale" if age > max_age_seconds else "healthy"
    return {"state": state, "reason": "last success too old" if state == "stale" else "recent successful look",
            "last_success_at": health["last_success_at"], "age_seconds": max(0, int(age)), **info}


def service(run=subprocess.run, unit: str = UNIT) -> dict:
    """The observer's own systemd unit: running? how often restarted? Only reads; never starts or stops it."""
    props = ("ActiveState", "SubState", "NRestarts", "ExecMainStatus")
    try:
        r = run(["systemctl", "show", unit, "--no-pager", *(f"--property={p}" for p in props)],
                capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"state": "unknown", "reason": f"systemctl unavailable: {type(exc).__name__}"}
    found = dict(line.split("=", 1) for line in (r.stdout or "").splitlines() if "=" in line)
    if r.returncode != 0 or not all(p in found for p in props) or not found["NRestarts"].isdigit():
        return {"state": "unknown", "reason": "systemd query failed or incomplete"}
    running = found["ActiveState"] == "active" and found["SubState"] == "running"
    return {"state": "active" if running else "inactive", "active_state": found["ActiveState"],
            "sub_state": found["SubState"], "restarts": int(found["NRestarts"]),
            "last_exit_status": found["ExecMainStatus"]}


def status(directory: Path | None = None, max_age_seconds: int = 180, run=subprocess.run) -> dict:
    """Everything an agent asks first: is the caretaker alive, and what did it last see?"""
    directory = directory or store.state_dir()
    latest = store.read_json(directory / "latest.json", {}) or {}
    return {"caretaker": freshness(directory, max_age_seconds), "service": service(run),
            "level": latest.get("level"), "problems": latest.get("problems"), "observed_at": latest.get("time"),
            "foreground": latest.get("foreground"), "recent_repairs": store.tail_jsonl(directory / "actions.jsonl", 5)}
