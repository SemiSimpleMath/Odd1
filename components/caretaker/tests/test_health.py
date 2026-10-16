"""The health icon's summary, colours and panel output, from fake sources (no display, no live system)."""
import json
import sqlite3
import time
from datetime import datetime, timezone

import pytest

from odd1_caretaker import health, popup

NOW = 1_800_000_000.0


def look(tmp_path, mem_pct=50, disk_pct=40, failed=(), io60=1.0):
    total = 8_000_000_000
    (tmp_path / "latest.json").write_text(json.dumps({
        "cpus": 4, "load": [1.0, 1.0, 1.0], "failed_units": list(failed), "problems": [],
        "memory": {"MemTotal": total, "MemAvailable": int(total * (1 - mem_pct / 100))},
        "disks": {"/": {"used_pct": disk_pct, "free": 5_000_000_000, "total": 25_000_000_000}},
        "pressure": {k: {"some": {"avg60": io60 if k == "io" else 0.0}} for k in ("cpu", "memory", "io")}}))
    fresh = datetime.now(timezone.utc).isoformat()  # the caretaker looked just now
    (tmp_path / "health.json").write_text(json.dumps({"ok": True, "last_success_at": fresh}))
    return tmp_path


def summary(tmp_path, **kw):
    s = {"machine": health.machine(look(tmp_path, **kw)), "agents": {"working": [], "runs_today": {}},
         "backups": {"daily": NOW - 3600 * 5, "hourly": NOW - 1200},
         "deploy": health.deploy({"current": "abcdef1234", "history": [{"at": NOW - 60, "ok": True, "action": "switch",
                                                                         "actor": "person:agent", "reason": "x"}]},
                                 now=NOW)}
    s["level"], s["reasons"] = health.assess(s, NOW)
    return s


def test_a_healthy_system_is_green_with_no_reasons(tmp_path):
    s = summary(tmp_path)
    assert (s["level"], s["reasons"]) == ("ok", [])


def test_amber_and_red_say_why_worst_first(tmp_path):
    s = summary(tmp_path, mem_pct=88, disk_pct=95, failed=["nginx.service"])
    assert s["level"] == "bad"
    assert s["reasons"][0] == "service failed: nginx.service" and "disk 95% full (5.0 GB free)" in s["reasons"]
    assert "memory 88% used" in s["reasons"]
    s = summary(tmp_path, io60=30)
    assert (s["level"], s["reasons"]) == ("warn", ["io pressure 30%"])


def test_old_backups_and_a_rolled_back_deploy_need_a_look(tmp_path):
    s = summary(tmp_path)
    s["backups"] = {"daily": NOW - 2 * 86400, "hourly": None}
    s["deploy"]["last"]["ok"] = False
    level, reasons = health.assess(s, NOW)
    assert level == "warn"
    assert reasons == ["last daily backup 2 days ago", "no hourly backup found",
                       "the last deploy failed and was rolled back"]


def test_a_broken_source_is_a_reason_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(health, "backups", lambda: 1 / 0)
    monkeypatch.setattr(health, "machine", lambda: health.machine(look(tmp_path)))
    monkeypatch.setattr(health, "agents", lambda: {"working": []})
    monkeypatch.setattr(health, "deploy", lambda: {"error": "odd1-deploy status gave nothing"})
    s = health.summary(NOW)
    assert "ZeroDivisionError" in s["backups"]["error"] and s["level"] == "warn"
    assert any(r.startswith("deploy: could not read") for r in s["reasons"])


def test_agents_come_from_the_task_store_read_only(tmp_path):
    db = tmp_path / "tasks.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, goal TEXT, status TEXT, claim_agent TEXT)")
    con.execute("CREATE TABLE log(id INTEGER PRIMARY KEY, task_id INT, at REAL, actor TEXT, kind TEXT)")
    con.execute("INSERT INTO tasks VALUES (73, 'Pin nixpkgs', 'claimed', 'fixer'), (2, 'x', 'done', NULL)")
    now = time.time()
    con.executemany("INSERT INTO log(task_id, at, actor, kind) VALUES (?, ?, ?, ?)",
                    [(73, now - 300, "fixer", "claim"), (73, now - 299, "fixer", "run"), (2, now - 10, "manager", "run"),
                     (2, now - 3 * 86400, "manager", "run")])
    con.commit()
    attention = [{"who": "person"}, {"who": "person"}, {"who": "manager"}]
    a = health.agents(db, attention, now)
    assert a["working"] == [{"role": "fixer", "task": 73, "goal": "Pin nixpkgs", "minutes": 5}]
    assert a["runs_today"] == {"fixer": 1, "manager": 1} and (a["need_person"], a["with_manager"]) == (2, 1)
    assert "error" in health.agents(tmp_path / "missing.db", [], now)


def test_backups_are_found_by_their_folder_names(tmp_path):
    (tmp_path / "2026-10-15").mkdir()
    (tmp_path / "2026-10-16").mkdir()
    (tmp_path / "home" / "2026-10-16T04-00").mkdir(parents=True)
    (tmp_path / "home" / "junk").mkdir()
    b = health.backups(tmp_path)
    assert time.strftime("%Y-%m-%d", time.localtime(b["daily"])) == "2026-10-16"
    assert time.strftime("%H:%M", time.localtime(b["hourly"])) == "04:00"


def test_the_panel_shows_the_icon_its_reasons_and_opens_the_popup(tmp_path):
    s = summary(tmp_path, disk_pct=90)
    s["agents"] = {"working": [{"role": "fixer", "task": 73}], "need_person": 2}
    out = health.panel(s, icon_path=tmp_path / "health-warn.png")
    assert out.startswith("<img>") and "health-warn" in out and "<click>odd1-caretaker popup</click>" in out
    assert "<tool>Needs a look\n• disk 90% full (5.0 GB free)\nAgents: fixer on #73\n2 waiting for you" in out


def test_the_icon_is_drawn_once_per_level(tmp_path):
    pytest.importorskip("PIL")
    path = health.icon("bad", tmp_path)
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and health.icon("bad", tmp_path) == path


def test_the_popup_rows(tmp_path):
    s = summary(tmp_path, mem_pct=71)
    s["agents"] = {"working": [{"role": "fixer", "task": 73, "goal": "Pin nixpkgs", "minutes": 5}],
                   "runs_today": {"fixer": 3, "manager": 9}, "need_person": 2, "with_manager": 1, "chats": 4}
    rows = popup.rows(s, NOW)
    assert rows[0] == ("title", "ok", "All good")
    assert ("bar", "Memory", 71.0, "71%  (5.7 GB of 8.0 GB)") in rows
    assert ("line", "fixer", "#73 Pin nixpkgs", "working (5 min)") in rows
    assert ("line", "Runs today", "12", "manager 9, fixer 3") in rows
    assert ("line", "Running", "abcdef1, deployed 1 min ago by you", "x") in rows
