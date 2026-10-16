"""Tickets worked outside the runners: closed by their check (rerun by the task system where a runner would), person
close and reopen, and rechecks that flag a ticket whose check fails later. Each test name has "verify" and "ready"
(task #36's done_when selects them that way)."""
import sqlite3

import pytest
from conftest import PERSON, approved

from odd1_tasks import attention, closing, mcp_server, roles
from odd1_tasks.store import TaskError


@pytest.fixture
def runner_env(monkeypatch):
    monkeypatch.setattr(closing, "unit_env", lambda role: {"PATH": "/usr/bin:/bin:/run/current-system/sw/bin",
                                                           "ROLE_MARK": role})
    monkeypatch.setattr(closing, "source", lambda: "chat test-chat")


def test_verify_ready_close_reruns_the_check_in_the_runners_folder_and_environment(store, runner_env):
    folder = roles.folder(store, "housekeeper")
    (folder / "made-by-chat").write_text("x")
    t = approved(store, done_when='test "$ROLE_MARK" = housekeeper && test -f made-by-chat')
    done = closing.close_by_check(store, t["id"], PERSON)
    assert done["status"] == "done"
    check = [e for e in store.show(t["id"])["log"] if e["kind"] == "check"][-1]
    assert check["data"]["exit"] == 0 and str(folder) in check["text"] and "housekeeper runner" in check["text"]


def test_verify_ready_a_failing_check_leaves_it_ready_and_uses_no_attempt(store, runner_env):
    t = approved(store, done_when="false")  # the chat says it is done; the check says otherwise: the check wins
    after = closing.close_by_check(store, t["id"], PERSON)
    assert after["status"] == "ready" and after["attempts"] == 0 and after["claim_agent"] is None
    assert "not closed: done_when exited 1" in after["log"][-1]["text"]


def test_verify_ready_only_an_approved_ready_ticket_with_its_approved_check(store, runner_env):
    proposed = store.create("g", "true", ["housekeeping"], actor=PERSON)
    with pytest.raises(TaskError, match="approved, ready"):
        closing.close_by_check(store, proposed["id"], PERSON)
    t = approved(store, done_when="true")
    with pytest.raises(sqlite3.IntegrityError):  # the approved check cannot be swapped for an easier one
        store.db.execute("UPDATE tasks SET done_when = 'true || true' WHERE id = ?", (t["id"],))
    store.claim("housekeeper", t["id"])
    with pytest.raises(TaskError, match="approved, ready"):
        closing.close_by_check(store, t["id"], PERSON)


def test_verify_ready_the_log_says_closed_by_check_from_which_chat_with_no_claimant(store, runner_env):
    t = approved(store, done_when="true")
    done = closing.close_by_check(store, t["id"], PERSON)
    assert done["claim_agent"] is None and done["attempts"] == 0
    kinds = [(e["kind"], e["actor"]) for e in done["log"]][-3:]
    assert kinds == [("close-request", PERSON), ("check", "tasks"), ("result", "tasks")]
    assert done["log"][-1]["text"] == "done (closed by check, from chat test-chat)"


def test_verify_ready_a_recheck_flags_a_closed_ticket_for_the_person_and_it_can_be_reopened(store, runner_env):
    folder = roles.folder(store, "housekeeper")
    (folder / "made-by-chat").write_text("x")
    t = approved(store, done_when="test -f made-by-chat")
    closing.close_by_check(store, t["id"], PERSON)
    assert closing.recheck(store) == []
    (folder / "made-by-chat").unlink()
    assert [f["id"] for f in closing.recheck(store)] == [t["id"]]
    assert closing.recheck(store) == []  # flagged once, not every hour
    mine = [i for i in attention.for_whom(attention.items(store), attention.PERSON) if i["task"] == t["id"]]
    assert [i["kind"] for i in mine] == ["flagged"]
    again = store.reopen(t["id"], PERSON, "the file is gone; redo it")
    assert again["status"] == "ready" and again["log"][-1]["kind"] == "reopen"
    assert [i for i in attention.items(store) if i["task"] == t["id"]] == []


def test_verify_ready_close_and_reopen_are_the_persons_with_a_reason(store, runner_env, monkeypatch):
    t = approved(store, done_when="false")  # a check no runner can pass (a person verifies by eye)
    with pytest.raises(TaskError, match="only a person"):
        store.close(t["id"], "housekeeper", "done")
    with pytest.raises(TaskError, match="say why"):
        store.close(t["id"], PERSON, " ")
    done = store.close(t["id"], PERSON, "checked on screen: the title is readable")
    assert done["status"] == "done" and "checked on screen" in done["log"][-2]["text"]
    with pytest.raises(TaskError, match="only a person"):
        store.reopen(t["id"], "housekeeper", "x")
    monkeypatch.setenv("ODD1_TASKS_AGENT", "housekeeper")  # the MCP tools refuse role agents, as tasks_approve does
    assert "error" in mcp_server.tasks_reopen(t["id"], "x")
    assert "error" in mcp_server.tasks_close_by_check(t["id"])
    monkeypatch.delenv("ODD1_TASKS_AGENT")
    assert mcp_server.tasks_reopen(t["id"], "not done after all")["status"] == "ready"


def test_decision_records_need_the_persons_approval_to_land(store):
    from odd1_tasks import landing
    by_person = approved(store, done_when="true")
    t = store.create("A rule change", "true", ["friction"], actor="fixer")
    roles.seed(store, "manager")
    store.db.execute("UPDATE agents SET decides = '[\"friction\"]' WHERE name = 'manager'")
    by_agent = store.approve(t["id"], "manager")
    rules = ["wiki/decisions/0002-the-persons-wishes-come-first.md", "components/x.py"]
    assert landing.decision_records_need_the_person(store.show(by_person["id"]), rules) is None
    refused = landing.decision_records_need_the_person(store.show(by_agent["id"]), rules)
    assert "decision records are the person's" in refused and "approved by manager" in refused
    assert landing.decision_records_need_the_person(store.show(by_agent["id"]), ["wiki/index.md"]) is None
