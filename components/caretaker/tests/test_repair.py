"""Restart-and-verify, and its record."""
import pytest

from fakes import Runner
from odd1_caretaker import repair, store


def test_restart_verifies_and_records(tmp_path):
    run = Runner([(lambda a: "is-active" in a, (0, "active\n", "")), (lambda a: True, (0, "", ""))])
    r = repair.restart("nginx.service", tmp_path, run, sleep=lambda s: None)
    assert r["ok"] and r["state"] == "active"
    verbs = [c[c.index("systemctl") + 1] for c in run.calls]
    assert verbs[:3] == ["reset-failed", "restart", "is-active"]
    assert store.tail_jsonl(tmp_path / "actions.jsonl", 5)[-1]["unit"] == "nginx.service"


def test_restart_that_does_not_come_back_says_so(tmp_path):
    run = Runner([(lambda a: "is-active" in a, (3, "failed\n", "")), (lambda a: True, (0, "", ""))])
    r = repair.restart("nginx.service", tmp_path, run, sleep=lambda s: None)
    assert not r["ok"] and "failed" in r["error"]
    run = Runner([(lambda a: "restart" in a, (1, "", "Access denied")), (lambda a: True, (0, "", ""))])
    r = repair.restart("nginx.service", tmp_path, run, sleep=lambda s: None)
    assert not r["ok"] and "Access denied" in r["error"]


def test_rejects_non_units(tmp_path):
    with pytest.raises(ValueError):
        repair.restart("nginx; rm -rf /", tmp_path, Runner([]))
