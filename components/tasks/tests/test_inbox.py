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
