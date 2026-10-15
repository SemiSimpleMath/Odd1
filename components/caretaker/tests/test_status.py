"""Freshness states and the read-only service query."""
import json
import os
from datetime import datetime, timedelta, timezone

from fakes import Runner
from odd1_caretaker import status

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def _write(d, health, latest=True):
    (d / "health.json").write_text(json.dumps(health))
    if latest:
        (d / "latest.json").write_text("{}")
        os.utime(d / "latest.json", (NOW.timestamp(), NOW.timestamp()))


def test_states(tmp_path):
    assert status.freshness(tmp_path, now=NOW)["state"] == "unknown"
    _write(tmp_path, {"ok": True, "last_success_at": (NOW - timedelta(seconds=30)).isoformat()})
    assert status.freshness(tmp_path, now=NOW)["state"] == "healthy"
    _write(tmp_path, {"ok": True, "last_success_at": (NOW - timedelta(seconds=999)).isoformat()})
    assert status.freshness(tmp_path, now=NOW)["state"] == "stale"
    _write(tmp_path, {"ok": True, "last_success_at": (NOW + timedelta(hours=1)).isoformat()})
    assert status.freshness(tmp_path, now=NOW)["state"] == "unknown"
    _write(tmp_path, {"ok": True, "last_success_at": "2026-10-15T12:00:00"})
    assert "timezone" in status.freshness(tmp_path, now=NOW)["reason"]
    _write(tmp_path, {"ok": "yes"})
    assert status.freshness(tmp_path, now=NOW)["state"] == "unknown"
    _write(tmp_path, {"ok": False, "last_error_at": NOW.isoformat(), "last_error": "boom"})
    s = status.freshness(tmp_path, now=NOW)
    assert s["state"] == "unhealthy" and s["report_age_seconds"] == 0


def test_service_query():
    out = "ActiveState=active\nSubState=running\nNRestarts=2\nExecMainStatus=0\n"
    s = status.service(Runner([(lambda a: True, (0, out, ""))]))
    assert s == {"state": "active", "active_state": "active", "sub_state": "running", "restarts": 2,
                 "last_exit_status": "0"}
    assert status.service(Runner([(lambda a: True, (0, "ActiveState=active\n", ""))]))["state"] == "unknown"
