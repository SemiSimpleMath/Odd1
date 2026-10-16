"""Closing work (decline, cancel), delegated decisions, who has to act on what, telling the person, the change feed,
run_if, and the migration of an older store."""
import json
import sqlite3
import time

import pytest
from conftest import PERSON, approved

from odd1_tasks import attention, console, roles, runner
from odd1_tasks.store import SCHEMA, Store, TaskError


@pytest.fixture
def managed(store, monkeypatch):
    """The store with the manager role seeded too, every runner running."""
    monkeypatch.setattr(console, "_runner", lambda role: "")
    roles.seed(store, "manager")
    return store


def who(store, task_id):
    return {i["task"]: i["who"] for i in attention.items(store)}.get(task_id)


def test_decline_and_cancel_close_work_with_a_reason(store):
    t = store.create("x", "true", ["housekeeping"], actor="housekeeper")
    with pytest.raises(TaskError, match="say why"):
        store.decline(t["id"], PERSON, " ")
    assert store.decline(t["id"], PERSON, "duplicate of #2")["status"] == "declined"
    assert store.show(t["id"])["log"][-1]["text"] == "declined: duplicate of #2"
    with pytest.raises(TaskError, match="cannot be declined"):
        store.decline(t["id"], PERSON, "again")
    done = approved(store, "finished")
    store.claim("housekeeper", done["id"])
    store.finish(done["id"], "housekeeper")
    store.verify(done["id"], "runner:housekeeper")
    with pytest.raises(TaskError, match="stays done"):
        store.cancel(done["id"], PERSON, "too late")  # only a recurring task is cancelled after it is done


def test_cancel_stops_a_running_agent_at_its_next_renewal(store, monkeypatch):
    monkeypatch.setenv("STUB_PLAN", json.dumps({"sleep": 60, "finish": True}))
    monkeypatch.setattr(runner, "RENEW_SECONDS", 0.3)
    t = approved(store, done_when="true")
    import threading
    stopper = threading.Timer(1.5, lambda: Store(store.state).cancel(t["id"], PERSON, "not needed any more"))
    stopper.start()
    start = time.monotonic()
    result = runner.run_once(store, "housekeeper")
    assert result["status"] == "cancelled" and time.monotonic() - start < 20
    kinds = [(e["kind"], e["text"][:16]) for e in store.show(t["id"])["log"]]
    assert ("budget", "the task is canc") in kinds and "check" not in [k for k, _ in kinds]  # stopped, not checked


def test_cancelling_a_recurring_task_ends_its_series(store):
    root = store.create("daily", "true", ["updates"], actor=PERSON, schedule="1d")
    store.approve(root["id"], PERSON)
    store.claim("update-watcher")
    store.finish(root["id"], "update-watcher")
    store.verify(root["id"], "runner:update-watcher")
    store.cancel(root["id"], PERSON, "not needed")
    store.maintain(now=time.time() + 3 * 86400)
    assert [t for t in store.list() if t["parent_id"] == root["id"]] == []


def test_a_role_decides_only_what_a_person_delegated_and_never_its_own(managed):
    store = managed
    mine = store.create("Clean old logs", "true", ["housekeeping"], actor="housekeeper")
    other = store.create("Delete ~/Videos", "true", ["needs-person"], actor="housekeeper")
    own = store.create("Something", "true", ["housekeeping"], actor="manager")
    assert store.approve(mine["id"], "manager")["approved_by"] == "manager"
    for task in (other, own):
        with pytest.raises(TaskError, match="may not decide"):
            store.approve(task["id"], "manager")
    with pytest.raises(TaskError, match="may not decide"):
        store.decline(own["id"], "housekeeper", "no")  # a role with no `decides` decides nothing
    ticket = store.create("A tool fought me", "true", ["friction", "desktop"], actor="housekeeper")
    assert store.decline(ticket["id"], "manager", "duplicate of #3")["status"] == "declined"  # one tag delegated
    mixed = store.create("Delete it", "true", ["friction", "needs-person"], actor="housekeeper")
    with pytest.raises(TaskError, match="may not decide"):
        store.approve(mixed["id"], "manager")  # needs-person is never delegated


def test_attention_routes_routine_to_the_manager_and_the_rest_to_the_person(managed):
    store = managed
    routine = store.create("Clean old logs", "true", ["housekeeping"], actor="housekeeper")
    risky = store.create("Delete ~/Videos", "true", ["needs-person"], actor="housekeeper")
    orphan = approved(store, "Print a poster", tags=["printing"])
    assert who(store, routine["id"]) == "manager"
    assert who(store, risky["id"]) == "person"
    assert who(store, orphan["id"]) == "person"  # approved, but no role takes printing, and it is not delegated
    backlog = approved(store, "A ticket", tags=["friction", "desktop"])
    assert who(store, backlog["id"]) is None  # approved friction is backlog for people's chats: nobody's decision
    store.escalate(routine["id"], "manager", "it deletes logs the person may want")
    assert who(store, routine["id"]) == "person"
    store.log(routine["id"], PERSON, "fine, go ahead")  # a person acted: the escalation is answered
    assert who(store, routine["id"]) == "manager"
    later = time.time() + 3 * 3600  # the manager left it longer than escalate_after_minutes
    item = next(i for i in attention.items(store, later) if i["task"] == routine["id"])
    assert item["who"] == "person" and "has not decided" in item["text"]


def test_a_paused_or_stopped_manager_hands_its_decisions_to_the_person(managed, monkeypatch):
    store = managed
    t = store.create("Clean old logs", "true", ["housekeeping"], actor="housekeeper")
    monkeypatch.setattr(console, "_runner", lambda role: "inactive" if role == "manager" else "")
    found = attention.items(store)
    assert who(store, t["id"]) == "person"
    assert any(i["key"] == "runner:manager" and i["who"] == "person" for i in found)


def test_the_person_is_told_once_in_one_notification(managed):
    store = managed
    for n in range(3):
        store.create(f"Risky {n}", "true", ["needs-person"], actor="housekeeper")
    store.create("Routine", "true", ["housekeeping"], actor="housekeeper")  # the manager's: not told
    sent = []
    told = attention.tell_person(store, send=lambda title, body: sent.append((title, body)))
    # 3 risky + the manager's own starter (only a person approves the manager); the other roles' starters
    # (housekeeping, updates) are the manager's to decide
    assert len(told) == 4 and sent[0][0] == "4 things need you"
    assert attention.tell_person(store, send=lambda *a: sent.append(a)) == [] and len(sent) == 1  # once
    assert attention.tell_person(store, now=time.time() + 90000, send=lambda *a: sent.append(a))  # a day on


def test_run_if_keeps_a_ready_task_from_starting_until_there_is_work(store, tmp_path):
    flag = tmp_path / "work"
    t = approved(store, run_if=f"test -e {flag}")
    assert store.claim("housekeeper") is None
    flag.touch()
    assert store.claim("housekeeper")["id"] == t["id"]


def test_the_change_feed_catches_a_reader_up_from_its_cursor(store):
    feed = store.changes()
    t = approved(store, "new")
    after = store.changes(feed["cursor"])
    assert [(c["task_id"], c["kind"]) for c in after["changes"]] == [(t["id"], "create"), (t["id"], "approve")]
    assert after["changes"][-1]["status"] == "ready" and store.changes(after["cursor"])["changes"] == []


def test_an_older_store_is_migrated_with_every_task_and_log_entry(tmp_path):
    old = (SCHEMA.replace(", 'declined', 'cancelled'", "").replace("  run_if TEXT,\n", "")
           .replace(",\n  decides TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(decides))", ""))
    db = sqlite3.connect(tmp_path / "tasks.db")
    db.executescript(old)
    db.execute("INSERT INTO tasks(goal, done_when, tags, created_by, created_at, status, updated_at)"
               " VALUES ('old', 'true', '[\"housekeeping\"]', 'person:x', 0, 'proposed', 0)")
    db.execute("INSERT INTO log(task_id, at, actor, kind) VALUES (1, 0, 'person:x', 'create')")
    db.commit()
    db.close()
    store = Store(tmp_path)
    assert store.decline(1, PERSON, "migrated")["status"] == "declined"
    assert [e["kind"] for e in store.show(1)["log"]] == ["create", "decline"]
    with pytest.raises(sqlite3.DatabaseError):
        store.db.execute("DELETE FROM tasks WHERE id = 1")  # the triggers came back


def test_person_first_their_tasks_are_claimed_before_agents_and_their_cancel_stops_at_once(store, monkeypatch):
    agents_own = store.create("an agent's own idea", "true", ["housekeeping"], actor="update-watcher")
    store.approve(agents_own["id"], PERSON)
    persons = approved(store, "the person's request", done_when="true")
    assert store.claim("housekeeper")["id"] == persons["id"]  # older agent work waits (rule 1)
    store.release(persons["id"], "housekeeper")
    monkeypatch.setenv("STUB_PLAN", json.dumps({"sleep": 60, "finish": True}))
    monkeypatch.setattr(runner, "RENEW_SECONDS", 120)  # no renewal comes: the status watch must stop it
    monkeypatch.setattr(runner, "WATCH_SECONDS", 0.2)
    import threading
    threading.Timer(1.0, lambda: Store(store.state).cancel(persons["id"], PERSON, "changed my mind")).start()
    start = time.monotonic()
    assert runner.run_once(store, "housekeeper")["status"] == "cancelled"
    assert time.monotonic() - start < 10
