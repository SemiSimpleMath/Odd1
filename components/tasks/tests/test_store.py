"""The store's rules, including against raw SQL (the triggers hold them, not the CLI)."""
import sqlite3
import time

import pytest
from conftest import PERSON, approved

from odd1_tasks.store import Store, TaskError


def kinds(store, tid):
    return [e["kind"] for e in store.show(tid)["log"]]


def test_new_task_waits_for_a_person(store):
    t = store.create("Clean up", "true", ["housekeeping"], actor="housekeeper")
    assert t["status"] == "proposed" and t["created_by"] == "housekeeper"
    assert store.claim("housekeeper") is None
    with pytest.raises(TaskError, match="only a person"):
        store.approve(t["id"], "housekeeper")
    assert store.approve(t["id"], PERSON)["status"] == "ready"
    assert store.claim("housekeeper")["id"] == t["id"]


def test_each_role_claims_only_its_tags(store):
    house = approved(store, "house", tags=["housekeeping"])
    upd = approved(store, "upd", tags=["updates"])
    with pytest.raises(TaskError, match="tag"):
        store.claim("update-watcher", house["id"])
    assert store.claim("update-watcher")["id"] == upd["id"]
    assert store.claim("update-watcher") is None  # the housekeeping task is not offered to it
    assert store.claim("housekeeper")["id"] == house["id"]


def test_rules_hold_against_raw_sql(store):
    t = store.create("x", "true", ["housekeeping"], actor=PERSON)
    db = sqlite3.connect(store.state / "tasks.db", isolation_level=None)  # autocommit, like the sqlite3 shell
    refused = [
        f"UPDATE tasks SET status = 'ready' WHERE id = {t['id']}",  # approval must name who approved
        "INSERT INTO tasks(goal, done_when, tags, created_by, created_at, status, updated_at)"
        " VALUES ('y', 'true', '[\"housekeeping\"]', 'agent', 0, 'ready', 0)",  # new tasks start proposed
        f"DELETE FROM tasks WHERE id = {t['id']}",
        f"UPDATE log SET text = 'forged' WHERE task_id = {t['id']}",
        f"DELETE FROM log WHERE task_id = {t['id']}",
    ]
    for sql in refused:
        with pytest.raises(sqlite3.DatabaseError):
            db.execute(sql)
    store.approve(t["id"], PERSON)
    refused = [
        f"UPDATE tasks SET done_when = 'true || x' WHERE id = {t['id']}",  # fixed after approval
        f"UPDATE tasks SET status = 'done' WHERE id = {t['id']}",  # ready > done is not a move
        f"UPDATE tasks SET status = 'claimed', claim_agent = 'update-watcher', lease_until = 9e9 WHERE id = {t['id']}",
    ]
    for sql in refused:
        with pytest.raises(sqlite3.DatabaseError):
            db.execute(sql)
    db.execute(f"UPDATE tasks SET status = 'claimed', claim_agent = 'housekeeper', lease_until = 9e9 "
               f"WHERE id = {t['id']}")
    db.execute(f"UPDATE tasks SET status = 'verifying' WHERE id = {t['id']}")
    with pytest.raises(sqlite3.DatabaseError, match="done_when"):
        db.execute(f"UPDATE tasks SET status = 'done' WHERE id = {t['id']}")  # no passing check logged


def test_claimed_tasks_never_share_a_resource(store):
    a = approved(store, "a", resources=["/tmp"])
    b = approved(store, "b", resources=["/tmp/x"])
    c = approved(store, "c", resources=["/srv"])
    assert store.claim("housekeeper")["id"] == a["id"]
    assert store.claim("housekeeper")["id"] == c["id"]  # b skipped: /tmp/x is inside /tmp
    with pytest.raises(TaskError, match="resource"):
        store.claim("housekeeper", b["id"])
    store.release(a["id"], "housekeeper")
    assert store.claim("housekeeper", b["id"])["status"] == "claimed"


def test_dependencies_come_first(store):
    first = approved(store, "first")
    second = approved(store, "second", depends_on=[first["id"]], priority=9)
    assert store.claim("housekeeper")["id"] == first["id"]
    assert store.claim("housekeeper") is None
    store.finish(first["id"], "housekeeper")
    store.verify(first["id"], "runner:housekeeper")
    assert store.claim("housekeeper")["id"] == second["id"]


def test_expired_lease_returns_task_to_ready(store):
    t = approved(store)
    store.claim("housekeeper", lease=0.05)
    time.sleep(0.1)
    assert store.show(t["id"])["status"] == "ready"
    assert "expired" in store.show(t["id"])["log"][-1]["text"]
    with pytest.raises(TaskError):
        store.renew(t["id"], "housekeeper")


def test_done_only_when_check_passes_else_retry_then_park(store, tmp_path):
    flag = tmp_path / "flag"
    t = approved(store, done_when=f"test -e {flag}", max_attempts=2)
    for expected in ("ready", "parked"):
        store.claim("housekeeper", t["id"])
        store.finish(t["id"], "housekeeper")
        assert store.verify(t["id"], "runner:housekeeper")["status"] == expected
    with pytest.raises(TaskError):
        store.claim("housekeeper", t["id"])
    ok = approved(store, done_when=f"test -e {flag}")
    flag.touch()
    store.claim("housekeeper", ok["id"])
    store.finish(ok["id"], "housekeeper")
    assert store.verify(ok["id"], "runner:housekeeper")["status"] == "done"
    assert kinds(store, ok["id"]) == ["create", "approve", "claim", "finish", "check", "result"]


def test_blocked_waits_for_a_person(store):
    t = approved(store)
    store.claim("housekeeper")
    assert store.release(t["id"], "housekeeper", blocked="needs a password")["status"] == "blocked"
    assert store.claim("housekeeper") is None
    with pytest.raises(TaskError):
        store.approve(t["id"], "housekeeper")
    assert store.approve(t["id"], PERSON)["status"] == "ready"


def test_an_agent_touches_only_the_task_it_holds(store):
    t = approved(store)
    with pytest.raises(TaskError):
        store.log(t["id"], "housekeeper", "not mine yet")
    store.claim("housekeeper")
    for op in (store.finish, store.release, store.renew):
        with pytest.raises(TaskError, match="claimed by housekeeper"):
            op(t["id"], "update-watcher")


def test_approved_recurring_task_runs_again_without_asking(store):
    root = store.create("daily", "true", ["updates"], actor=PERSON, schedule="1d")
    store.maintain(now=time.time() + 5 * 86400)
    assert len(store.list()) == 2 + 1  # two starters + root: nothing spawned before approval
    store.approve(root["id"], PERSON)
    store.claim("update-watcher")
    store.finish(root["id"], "update-watcher")
    store.verify(root["id"], "runner:update-watcher")
    store.maintain()  # not yet a day later
    assert [t["id"] for t in store.list(status="ready")] == []
    store.maintain(now=time.time() + 86401)
    store.maintain(now=time.time() + 86402)  # one open run at a time
    runs = [t for t in store.list() if t["parent_id"] == root["id"]]
    assert [(r["status"], r["created_by"]) for r in runs] == [("ready", "schedule")]
    assert store.claim("update-watcher")["id"] == runs[0]["id"]


def test_store_is_shared_between_connections(store):
    t = approved(store)
    assert Store(store.state).claim("housekeeper")["id"] == t["id"]
    assert store.claim("housekeeper") is None
