"""Offline: fixture tasks.db, transcripts, caretaker and deploy history -> samples -> summary and trend."""
import json
import sqlite3
import time

from odd1_stats import cli, collect, report

NOW = time.mktime((2026, 10, 16, 12, 0, 0, 0, 0, -1))
DAY = collect.DAY


def make_db(path, events, tasks):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, tags TEXT, attempts INTEGER, created_at REAL)")
    db.execute("CREATE TABLE log(id INTEGER PRIMARY KEY, task_id INTEGER, at REAL, actor TEXT, kind TEXT, text TEXT,"
               " data TEXT DEFAULT '{}')")
    db.executemany("INSERT INTO tasks VALUES (?, ?, ?, ?)", [(i, json.dumps(t), a, c) for i, t, a, c in tasks])
    db.executemany("INSERT INTO log(task_id, at, actor, kind, text, data) VALUES (?, ?, ?, ?, ?, ?)",
                   [(t, at, actor, kind, text, json.dumps(data)) for t, at, actor, kind, text, data in events])
    db.commit()
    db.close()


def run(tid, at, role, turns, cost, result, budget=False):
    ev = [(tid, at, role, "run", "run started", {}), (tid, at + 600, f"runner:{role}", "run-end", "ended",
                                                       {"num_turns": turns, "total_cost_usd": cost, "exit": 0})]
    if budget:
        ev.append((tid, at + 600, f"runner:{role}", "budget", "turn budget reached", {}))
    ev.append((tid, at + 700, "runner", "result", result, {}))
    return ev


def transcript(path, lines):
    with open(path, "a") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def assistant(mid, tools=(), out=10):
    return {"type": "assistant", "message": {"id": mid, "usage": {"input_tokens": 100, "output_tokens": out},
                                             "content": [{"type": "tool_use", "id": i, "name": n} for i, n in tools]}}


def result(tid, error=False, text="ok"):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": error,
                                                     "content": text}]}}


def setup(tmp_path, monkeypatch):
    for d in ("state", "tasks", "projects/-home-agent", "projects/-var-lib-odd1-tasks-work-task-1", "caretaker",
              "deploy"):
        (tmp_path / d).mkdir(parents=True)
    monkeypatch.setenv("ODD1_STATS_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("ODD1_TASKS_STATE", str(tmp_path / "tasks"))
    monkeypatch.setenv("ODD1_STATS_PROJECTS", str(tmp_path / "projects"))
    monkeypatch.setenv("ODD1_CARETAKER_STATE", str(tmp_path / "caretaker"))
    monkeypatch.setenv("ODD1_DEPLOY_STATE", str(tmp_path / "deploy"))
    # before (8-14 days ago): 4 fixer runs, all done in 10 turns; recent (last 7 days): 4 runs, 30 turns, 1 parked
    events = []
    for i in range(4):
        events += run(i + 1, NOW - 10 * DAY + i * 3600, "fixer", 10, 0.5, "done")
    for i in range(4):
        events += run(i + 10, NOW - 2 * DAY + i * 3600, "fixer", 30, 2.0,
                      "check failed (3/3): parked, retries used up" if i == 0 else "done", budget=i == 0)
    events.append((20, NOW - DAY, "worker", "release", "blocked: needs a person", {}))
    tasks = [(i + 1, ["fixer"], 0, NOW - 11 * DAY) for i in range(4)]
    tasks += [(i + 10, ["friction", "screen"], 1 if i == 3 else 0, NOW - 3 * DAY) for i in range(4)]
    tasks.append((20, ["worker"], 0, NOW - DAY))
    make_db(tmp_path / "tasks" / "tasks.db", events, tasks)
    transcript(tmp_path / "caretaker" / "history.jsonl", [{
        "time": "x", "pressure": {"cpu": {"some": {"avg60": 1.5}}, "memory": {"some": {"avg60": 0.2}}},
        "memory": {"MemTotal": 1000, "MemAvailable": 400}, "disks": {"/": {"used_pct": 71.0}},
        "failed_units": ["a.service"], "level": "ok"}])
    transcript(tmp_path / "deploy" / "deployments.jsonl", [
        {"at": NOW - DAY, "action": "switch", "ok": True, "reason": ""},
        {"at": NOW - DAY, "action": "switch", "ok": True, "reason": "automatic rollback from abc: unit failed"}])
    return collect.paths()


def test_sample_summary_trend(tmp_path, monkeypatch, capsys):
    p = setup(tmp_path, monkeypatch)
    chat = tmp_path / "projects" / "-home-agent" / "s1.jsonl"
    transcript(chat, [assistant("m1", [("t1", "Bash")]), assistant("m1", [("t2", "mcp__screen__screen_click")]),
                      result("t1"), result("t2", True, "no such element"),
                      {"type": "user", "message": {"content": "[Request interrupted by user]"}}])
    s1 = collect.sample(p, now=NOW - 9 * DAY)
    assert s1["tasks"]["roles"]  # first sample backfills 30 days
    sess = s1["sessions"]["s1"]
    assert sess["turns"] == 1 and sess["tool_calls"] == 2 and sess["tool_errors"] == 1 and sess["interrupts"] == 1
    assert sess["source"] == "chat" and "content" not in json.dumps(sess)  # counts only, no text
    assert s1["system"]["mem_avail_pct"] == 40.0 and s1["system"]["failed_units"] == 1
    assert len(s1["deploys"]) == 2 and s1["deploys"][1]["rollback"]

    # a partial line is not consumed; the same message id continued is one turn
    with open(chat, "a") as f:
        f.write(json.dumps(assistant("m1")) + "\n" + '{"type": "assist')
    routine = tmp_path / "projects" / "-var-lib-odd1-tasks-work-task-1" / "r1.jsonl"
    calls = [assistant(f"r{i}", [(f"x{i}", "mcp__screen__screen_click")]) for i in range(12)]
    transcript(routine, calls + [result(f"x{i}", True, "Permission to use x denied") for i in range(6)])
    s2 = collect.sample(p, now=NOW - 60)
    assert "s1" not in s2["sessions"] and s2["deploys"] == []
    r1 = s2["sessions"]["r1"]
    assert r1["source"] == "routine" and r1["turns"] == 12 and r1["denials"] == 6
    assert r1["errors_by_tool"] == {"mcp__screen__screen_click": 6}

    s = report.summary(p["state"], 7, now=NOW)
    fixer = s["roles"]["fixer"]
    assert fixer["runs"] == 4 and fixer["done"] == 3 and fixer["parked"] == 1 and fixer["budget_hits"] == 1
    assert fixer["turns"] == 120 and fixer["done_after_retry"] == 1 and s["roles"]["worker"]["blocked"] == 1
    assert s["friction"]["screen"] == {"opened": 4, "closed": 3}
    assert s["servers"]["screen"] == {"calls": 12, "errors": 6} and s["usage"]["routine"]["sessions"] == 1
    assert s["deploys"] == {"switches": 0, "failed": 0, "rollbacks": 0}  # those were seen 9 days ago

    t = report.trend(p["state"], 7, now=NOW)
    assert "turns per done task" in t["regressions"] and "success rate" in t["regressions"]
    assert "cost per done task" in t["regressions"]

    monkeypatch.setattr(time, "time", lambda: NOW)
    assert cli.main(["summary", "--days", "7"]) == 0
    out = capsys.readouterr().out
    assert "turns" in out and "fixer" in out and "mcp__screen__screen_click" in out
    assert cli.main(["trend", "--days", "7"]) == 0
    assert "WORSE" in capsys.readouterr().out


def test_rotate_monthly_and_empty(tmp_path, monkeypatch):
    p = setup(tmp_path, monkeypatch)
    collect.sample(p, now=NOW - 20 * DAY)  # September
    collect.sample(p, now=NOW - 60)
    files = sorted(f.name for f in p["state"].glob("samples*.jsonl"))
    assert files == ["samples-2026-09.jsonl", "samples.jsonl"]
    assert report.summary(p["state"], 30, now=NOW)["samples"] == 2  # both months are read
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert report.trend(empty, 7, now=NOW)["regressions"] == []
    assert "turns" in report.format_summary(report.summary(empty, 7, now=NOW))
