"""Decisions the person makes: a page with options, pros, cons and a recommendation; a chat to discuss it first;
and a ticket changed on their decision instead of replaced."""
import sqlite3

import pytest
from conftest import PERSON, approved

from odd1_tasks import mcp_server, pages
from odd1_tasks.store import TaskError

OPTIONS = [{"label": "Remove the setting", "what": "drop viewMode from nix/odd1.nix",
            "pros": ["small", "reversible"], "cons": "no focus view"},
           {"label": "Switch renderers", "pros": ["focus view works"], "cons": ["every chat changes"]}]


def test_decision_page_shows_the_options_pros_cons_and_recommendation(store):
    t = approved(store, "The chats' focus view works or goes", done_when="true")
    store.escalate(t["id"], "manager", "which fix do you want?", OPTIONS, "Remove the setting: smallest")
    path = pages.write(store, t["id"])
    page = path.read_text()
    assert path == store.state / "pages" / f"task-{t['id']}.html"
    assert "which fix do you want?" in page and "Remove the setting" in page and "Switch renderers" in page
    assert '<li>reversible</li>' in page and '<li>no focus view</li>' in page and '<li>every chat changes</li>' in page
    assert 'option recommended' in page and "Remove the setting: smallest" in page
    assert "prefers-color-scheme: dark" in page and "&lt;" not in t["goal"]


def test_decision_page_escapes_what_agents_wrote(store):
    t = approved(store, "a <script>alert(1)</script> goal", done_when="true")
    store.escalate(t["id"], "manager", "x", [{"label": "<b>bold</b>", "pros": ["<i>"]}])
    page = pages.render(store.show(t["id"]))
    assert "<script>alert" not in page and "&lt;b&gt;bold&lt;/b&gt;" in page


def test_discuss_opens_a_chat_in_the_column_primed_with_the_ticket(store, monkeypatch):
    monkeypatch.setattr(pages.shutil, "which", lambda _: None)  # no terminal in the test: return the command
    t = approved(store, "a decision", done_when="true")
    store.escalate(t["id"], "manager", "approve or decline?", OPTIONS)
    command = pages.discuss(store, t["id"])
    assert "--role=odd1-agent:claude" in command and f"--title=Discuss #{t['id']}" in command
    assert "approve or decline?" in command[-1] and f"task-{t['id']}.html" in command[-1]
    assert "record it as theirs" in command[-1]


def test_edit_on_decision_changes_an_approved_ticket_in_place(store):
    t = approved(store, "old goal", done_when="grep -q x file")
    with pytest.raises(TaskError, match="only a person"):
        store.edit(t["id"], "manager", "better check", done_when="true")
    with pytest.raises(sqlite3.IntegrityError):  # nobody else changes an approved ticket
        store.db.execute("UPDATE tasks SET done_when = 'true' WHERE id = ?", (t["id"],))
    after = store.edit(t["id"], PERSON, "the person chose a real test", done_when="odd1-x-test -q -k thing")
    assert after["done_when"] == "odd1-x-test -q -k thing" and after["id"] == t["id"] and after["status"] == "ready"
    entry = after["log"][-1]
    assert entry["kind"] == "edit" and entry["data"]["before"] == {"done_when": "grep -q x file"}
    assert store.db.execute("SELECT count(*) FROM edit_grants").fetchone()[0] == 0  # the grant is gone again


def test_decision_page_escalation_options_through_mcp(store, monkeypatch):
    monkeypatch.setenv("ODD1_TASKS_AGENT", "manager")
    t = approved(store, "a decision", done_when="true")
    out = mcp_server.tasks_escalate(t["id"], "pick one", OPTIONS, "Switch renderers")
    assert out["id"] == t["id"]
    assert pages.decision(store.show(t["id"]))["recommendation"] == "Switch renderers"
