"""The task manager's inbox: asking, answering once, who may answer, the manager woken by a question, the answer's
way back to the asker's conversation, and a role agent reading it in its next run."""
import json
import os

import pytest
from conftest import PERSON

from odd1_tasks import attention, console, inbox, roles, runner
from odd1_tasks.store import TaskError


@pytest.fixture
def managed(store, monkeypatch):
    monkeypatch.setattr(console, "_runner", lambda role: "")
    roles.seed(store, "manager")
    return store


def test_a_question_waits_for_the_manager_and_is_answered_once(managed):
    task = managed.create("Disk under 80%", "true", ["housekeeping"], actor="housekeeper")
    q = managed.ask("odd1-3f", PERSON, "Who takes friction tickets now?", session="s-1", task_id=task["id"])
    item = next(i for i in attention.items(managed) if i["kind"] == "message")
    assert item["who"] == "manager" and item["key"] == f"message:{q['id']}" and "@odd1-3f" in item["text"]
    with pytest.raises(TaskError, match="only the task manager"):
        managed.reply(q["id"], "housekeeper", "me!")
    question, reply = managed.reply(q["id"], "manager", "The fixer, one run a day.")
    assert reply["recipient"] == "odd1-3f" and reply["reply_to"] == q["id"]
    assert not managed.inbox() and managed.inbox(answered=True)[0]["answer"] == "The fixer, one run a day."
    assert not [i for i in attention.items(managed) if i["kind"] == "message"], "answered: nothing waits"
    assert "answered message" in managed.show(task["id"])["log"][-1]["text"]  # the task's log keeps it
    with pytest.raises(TaskError, match="answered already"):
        managed.reply(q["id"], PERSON, "again")
    with pytest.raises(TaskError, match="is a reply"):
        managed.reply(reply["id"], PERSON, "to an answer")
    with pytest.raises(Exception, match="append-only"):
        managed.db.execute("UPDATE messages SET text = 'x'")


def test_without_a_running_manager_a_question_is_the_persons(store):
    q = store.ask("odd1-3f", PERSON, "Anyone there?")
    assert {i["key"]: i["who"] for i in attention.items(store)}[f"message:{q['id']}"] == "person"


def registry(tmp_path, pid, session_id, name):
    folder = tmp_path / "sessions"
    folder.mkdir(exist_ok=True)
    (folder / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": session_id, "name": name}))
    return folder


def test_the_asker_is_its_claude_code_session_found_up_the_process_tree(tmp_path):
    folder = registry(tmp_path, os.getppid(), "abc-123", "odd1-3f")  # the parent stands in for Claude Code
    assert inbox.own_session(sessions=folder) == {"id": "abc-123", "name": "odd1-3f"}
    assert inbox.own_session(sessions=tmp_path / "none") is None


def test_the_answer_goes_to_the_askers_live_session_by_its_current_name(managed, tmp_path):
    folder = registry(tmp_path, os.getpid(), "abc-123", "renamed-chat")  # a live process, renamed since
    q = managed.ask("odd1-3f", PERSON, "Where do replies go?", session="abc-123")
    question, reply = managed.reply(q["id"], "manager", "To you.")
    out = inbox.delivery(question, reply, sessions=folder)
    assert out["to"] == "renamed-chat" and out["message"].startswith("@renamed-chat The task manager answers")
    gone = inbox.delivery(question | {"session": "ended"}, reply, sessions=folder)
    assert gone["to"] is None and "next run" in gone["note"]


def test_a_role_agent_reads_its_answers_in_its_next_runs_prompt(managed):
    q = managed.ask("housekeeper", "housekeeper", "May I clean /var/tmp too?")
    managed.reply(q["id"], "manager", "No: only ~/.cache and /tmp.")
    task = managed.create("x", "true", ["housekeeping"], actor=PERSON)
    text = runner.prompt(managed.show(task["id"]), "housekeeper", 5, 2, replies=managed.replies("housekeeper"))
    assert "May I clean /var/tmp too?" in text and "No: only ~/.cache and /tmp." in text
    assert "tasks_ask" in text
    assert not managed.replies("fixer")


def test_a_subagents_question_is_answered_to_its_parent_who_passes_it_on(managed, tmp_path):
    folder = registry(tmp_path, os.getpid(), "abc-123", "odd1-3f")
    q = managed.ask("odd1-3f", PERSON, "Which role runs updates?", session="abc-123", agent="researcher")
    question, reply = managed.reply(q["id"], "manager", "The updater.")
    out = inbox.delivery(question, reply, sessions=folder)
    assert out["to"] == "odd1-3f" and out["message"].startswith("@odd1-3f, for your subagent researcher:")
    assert 'SendMessage(to="researcher")' in out["message"]


def test_an_asker_can_wait_for_the_answer_and_the_parent_still_hears_of_it(managed, tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(inbox, "POLL_S", 0.01)
    q = managed.ask("odd1-3f", PERSON, "Quick one?", session="abc-123", agent="researcher", wait_minutes=1)
    assert asyncio.run(inbox.wait_answer(managed, q["id"], 0.001)) is None  # nothing yet: gives up on time

    async def ask_and_answer():
        waiting = asyncio.ensure_future(inbox.wait_answer(managed, q["id"], 1))
        await asyncio.sleep(0.05)
        managed.reply(q["id"], "manager", "Yes.")
        return await waiting

    assert asyncio.run(ask_and_answer())["text"] == "Yes."
    folder = registry(tmp_path, os.getpid(), "abc-123", "odd1-3f")
    out = inbox.delivery(managed.message(q["id"]), managed.answer(q["id"]), sessions=folder)
    assert out["to"] == "odd1-3f" and "has it already" in out["message"], "sent anyway, marked as already had"


def test_an_older_store_gains_the_new_message_columns(tmp_path):
    import sqlite3
    from odd1_tasks.store import Store
    state = tmp_path / "old"
    state.mkdir()
    db = sqlite3.connect(state / "tasks.db")
    db.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, at REAL NOT NULL, sender TEXT NOT NULL, actor TEXT "
               "NOT NULL, session TEXT, recipient TEXT NOT NULL, task_id INTEGER, reply_to INTEGER, text TEXT NOT NULL)")
    db.execute("INSERT INTO messages(at, sender, actor, recipient, text) VALUES (1, 'a', 'b', 'manager', 'old one')")
    db.commit()
    db.close()
    store = Store(state)
    assert store.inbox()[0]["text"] == "old one" and store.inbox()[0]["agent"] is None
    assert store.ask("a", "b", "new", agent="x", wait_minutes=1)["agent"] == "x"
