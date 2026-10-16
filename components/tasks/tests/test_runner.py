"""The runner with a stub claude: roles, budgets, checks, leases surviving a killed runner, and the log."""
import json
import os
import signal
import subprocess
import sys
import time

import pytest
from conftest import COMPONENT, PERSON, approved

from odd1_tasks import roles, runner


def plan(monkeypatch, **kw):
    monkeypatch.setenv("STUB_PLAN", json.dumps(kw))


def calls():
    path = os.environ["STUB_RECORD"]
    return [json.loads(line) for line in open(path)] if os.path.exists(path) else []


def edit_role(store, role, **values):
    path = roles.folder(store, role) / "role.toml"
    text = path.read_text()
    for k, v in values.items():
        text = "\n".join(f"{k} = {v}" if line.startswith(f"{k} =") else line for line in text.splitlines())
    path.write_text(text)


def test_each_role_runs_only_its_own_tasks(store, monkeypatch, tmp_path):
    plan(monkeypatch, finish=True, log=["cleaned"])
    house = approved(store, "house", tags=["housekeeping"])
    upd = approved(store, "upd", tags=["updates"])
    assert runner.run_once(store, "update-watcher")["id"] == upd["id"]
    assert runner.run_once(store, "update-watcher") is None
    assert runner.run_once(store, "housekeeper")["id"] == house["id"]
    assert [(c["agent"], c["task"]) for c in calls()] == [("update-watcher", str(upd["id"])),
                                                          ("housekeeper", str(house["id"]))]
    call = calls()[1]
    assert call["cwd"] == str(roles.folder(store, "housekeeper"))
    args = " ".join(call["argv"])
    assert "--permission-mode dontAsk" in args and "--strict-mcp-config" in args
    assert f"--settings {roles.folder(store, 'housekeeper')}/settings.json" in args
    assert "dangerously" not in args and "bypass" not in args
    assert "Goal: house" in call["prompt"] and "Done when" in call["prompt"]


def test_proposed_never_runs_approved_recurring_runs_again(store, monkeypatch):
    plan(monkeypatch, finish=True)
    starter = [t for t in store.list() if t["created_by"] == "seed:update-watcher"][0]
    assert starter["status"] == "proposed" and starter["schedule"] == "1d"
    assert runner.run_once(store, "update-watcher") is None and calls() == []
    store.approve(starter["id"], PERSON)
    edit_role(store, "update-watcher", runs_per_day=9)
    reports = roles.folder(store, "update-watcher") / "reports"
    plan(monkeypatch, finish=True, touch=[str(reports / time.strftime("%Y-%m-%d.md"))])
    assert runner.run_once(store, "update-watcher")["status"] == "done"
    store.maintain(now=time.time() + 86401)  # the next day: a new run, ready, nobody asked
    nxt = store.list(status="ready")
    assert [(t["parent_id"], t["created_by"]) for t in nxt] == [(starter["id"], "schedule")]
    assert runner.run_once(store, "update-watcher")["id"] == nxt[0]["id"]


def test_killed_runner_task_returns_after_lease_and_next_run_sees_log(store, monkeypatch):
    t = approved(store, "long job", done_when="true")
    plan(monkeypatch, log=["step 1 of 2 done"], sleep=60)
    env = dict(os.environ, ODD1_TASKS_LEASE_SECONDS="5", ODD1_TASKS_CLAUDE=" ".join(runner.CLAUDE))
    proc = subprocess.Popen([sys.executable, "-m", "odd1_tasks.runner", "housekeeper", "--once"], env=env,
                            cwd=COMPONENT, stdout=subprocess.DEVNULL)
    deadline = time.time() + 20
    while not any(e["text"] == "step 1 of 2 done" for e in store.show(t["id"])["log"]):
        assert time.time() < deadline, "the stub never logged"
        time.sleep(0.1)
    proc.kill()  # the runner dies mid-task, and its claude with it
    os.killpg(calls()[0]["pid"], signal.SIGKILL)
    proc.wait()
    assert store.show(t["id"])["status"] == "claimed"  # nobody renews it now
    deadline = time.time() + 10
    while store.show(t["id"])["status"] != "ready":
        assert time.time() < deadline, "the lease never expired"
        time.sleep(0.2)
    plan(monkeypatch, finish=True)
    assert runner.run_once(store, "housekeeper")["status"] == "done"
    resumed = calls()[1]["prompt"]
    assert "step 1 of 2 done" in resumed and "lease of housekeeper expired" in resumed


def test_turn_budget_is_passed_and_recorded(store, monkeypatch):
    plan(monkeypatch, subtype="error_max_turns", turns=5)
    t = approved(store, max_turns=5)
    edit_role(store, "housekeeper", max_turns=3)  # the role caps the task: min(5, 3)
    runner.run_once(store, "housekeeper")
    argv = calls()[0]["argv"]
    assert argv[argv.index("--max-turns") + 1] == "3"
    texts = [e["text"] for e in store.show(t["id"])["log"]]
    assert "turn budget reached (3 turns): stopped" in texts


def test_time_budget_stops_the_agent(store, monkeypatch):
    plan(monkeypatch, sleep=60, finish=True)
    monkeypatch.setattr(runner, "RENEW_SECONDS", 0.2)
    t = approved(store, max_minutes=0.03, done_when="false")
    start = time.monotonic()
    assert runner.run_once(store, "housekeeper")["status"] == "ready"  # stopped, checked, failed: one retry used
    assert time.monotonic() - start < 15
    log = store.show(t["id"])["log"]
    assert any(e["kind"] == "budget" and "time budget" in e["text"] for e in log)
    assert not any(e["kind"] == "finish" and e["actor"] == "housekeeper" for e in log)  # killed before finishing


def test_runs_per_day(store, monkeypatch):
    plan(monkeypatch, finish=True)
    edit_role(store, "housekeeper", runs_per_day=1)
    approved(store, "one")
    approved(store, "two")
    assert runner.run_once(store, "housekeeper") is not None
    assert runner.run_once(store, "housekeeper") is None
    assert len(calls()) == 1


def test_failing_check_retries_then_parks_and_passing_check_is_done(store, monkeypatch, tmp_path):
    plan(monkeypatch, finish=True)
    t = approved(store, done_when="echo not yet; false", max_attempts=2)
    assert runner.run_once(store, "housekeeper")["status"] == "ready"
    assert runner.run_once(store, "housekeeper")["status"] == "parked"
    assert runner.run_once(store, "housekeeper") is None
    checks = [e for e in store.show(t["id"])["log"] if e["kind"] == "check"]
    assert [c["data"]["exit"] for c in checks] == [1, 1] and "not yet" in checks[0]["data"]["output"]
    flag = tmp_path / "made"
    plan(monkeypatch, touch=[str(flag)])  # no tasks_finish: the runner checks anyway, the check decides
    ok = approved(store, done_when=f"test -e {flag}")
    assert runner.run_once(store, "housekeeper")["status"] == "done"
    kinds = [e["kind"] for e in store.show(ok["id"])["log"]]
    assert kinds == ["create", "approve", "claim", "run", "run-end", "finish", "check", "result"]


def test_blocked_is_not_checked(store, monkeypatch):
    plan(monkeypatch, blocked="needs root")
    t = approved(store, done_when="true")
    assert runner.run_once(store, "housekeeper")["status"] == "blocked"
    assert "check" not in [e["kind"] for e in store.show(t["id"])["log"]]


def test_seed_keeps_the_persons_edits_and_proposes_starters_once(store):
    edit_role(store, "housekeeper", runs_per_day=7)
    roles.seed(store, "housekeeper")
    assert roles.load(store, "housekeeper")["runs_per_day"] == 7
    starters = [t for t in store.list() if t["created_by"].startswith("seed:")]
    assert sorted(t["created_by"] for t in starters) == ["seed:housekeeper", "seed:update-watcher"]
    assert {t["status"] for t in starters} == {"proposed"}
    assert os.access(roles.folder(store, "housekeeper") / "clean.sh", os.X_OK)


@pytest.mark.parametrize("role", ["housekeeper", "update-watcher"])
def test_role_settings_never_bypass_permissions(store, role):
    settings = json.loads((roles.folder(store, role) / "settings.json").read_text())
    perms = settings["permissions"]
    assert "mcp__tasks__tasks_approve" in perms["deny"] and "mcp__tasks__tasks_approve" not in perms["allow"]
    assert not any(a.startswith(("Bash(rm", "Bash(sudo", "Bash(find")) or a == "Bash" for a in perms["allow"])
    assert "defaultMode" not in perms


def test_seed_follows_the_repository_except_where_the_person_edited(store, tmp_path):
    source = tmp_path / "seed"
    (source / "r").mkdir(parents=True)
    (source / "r" / "role.toml").write_text('tags = ["x"]\n')
    (source / "r" / "CLAUDE.md").write_text("v1")
    roles.seed(store, "r", source)
    folder = roles.folder(store, "r")
    (folder / "role.toml").write_text('tags = ["x", "y"]\n')  # the person edits one file
    (source / "r" / "role.toml").write_text('tags = ["z"]\n')  # the repository changes both
    (source / "r" / "CLAUDE.md").write_text("v2")
    roles.seed(store, "r", source)
    assert (folder / "role.toml").read_text() == 'tags = ["x", "y"]\n'  # kept
    assert (folder / "CLAUDE.md").read_text() == "v2"  # followed


def test_a_paused_role_takes_no_new_task(store, monkeypatch):
    plan(monkeypatch, finish=True)
    approved(store)
    edit_role(store, "housekeeper", enabled="false")
    assert runner.run_once(store, "housekeeper") is None and calls() == []
    edit_role(store, "housekeeper", enabled="true")
    assert runner.run_once(store, "housekeeper")["status"] == "done"
