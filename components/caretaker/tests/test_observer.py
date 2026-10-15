"""The daemon's look-and-record cycle, the health contract, history bounds, and automatic repair limits."""
import json

import pytest

from fakes import Runner
from odd1_caretaker import observer, repair, status, store

SNAP = {"time": "t", "level": "ok", "problems": [], "failed_units": [], "foreground": {"unavailable": "x"}}


def test_success_then_failure_keeps_last_good(tmp_path):
    observer.run_once(tmp_path, snapshot=lambda: dict(SNAP))
    health = json.loads((tmp_path / "health.json").read_text())
    assert health["ok"] is True and health["last_error"] is None
    assert "Nothing needs attention" in (tmp_path / "latest.md").read_text()
    assert status.freshness(tmp_path)["state"] == "healthy"

    def broken():
        raise OSError("proc gone")
    with pytest.raises(OSError):
        observer.run_once(tmp_path, snapshot=broken)
    health2 = json.loads((tmp_path / "health.json").read_text())
    assert health2["ok"] is False and health2["last_success_at"] == health["last_success_at"]
    assert "proc gone" in health2["last_error"]
    assert json.loads((tmp_path / "latest.json").read_text())["time"] == "t"  # last good report untouched
    assert status.freshness(tmp_path)["state"] == "unhealthy"


def test_history_is_bounded(tmp_path):
    path = tmp_path / "history.jsonl"
    for i in range(25):
        store.append_bounded(path, {"i": i}, keep=10)
    rows = store.tail_jsonl(path, 100)
    assert 10 <= len(rows) < 20 and rows[-1] == {"i": 24}


def test_auto_repair_only_allowed_units_and_at_most_three_an_hour(tmp_path, monkeypatch):
    monkeypatch.setenv("ODD1_CARETAKER_RESTART", "nginx.service")
    run = Runner([(lambda a: "is-active" in a, (0, "active\n", "")), (lambda a: True, (0, "", ""))])

    def snap():
        return dict(SNAP, failed_units=["nginx.service", "other.service"], problems=[])
    for _ in range(4):
        last = observer.run_once(tmp_path, snapshot=snap, run=run, sleep=lambda s: None)
    restarts = [c for c in run.calls if "restart" in c]
    assert len(restarts) == 3 and all("nginx.service" in c for c in restarts)
    assert "repairs" not in last and any("keeps failing" in p for p in last["problems"])
    assert repair.recent_restarts("nginx.service", tmp_path) == 3
