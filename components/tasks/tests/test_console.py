"""The person's line to the task manager: the one-line status, and the console's folder and command."""
import json
import time

from conftest import PERSON, approved

from odd1_tasks import console


def test_status_says_who_works_on_what_and_what_waits(store, monkeypatch):
    monkeypatch.setattr(console, "_runner", lambda role: "")
    line = console.summary(store)
    assert line == "Tasks · housekeeper idle · update-watcher idle · 2 proposed"  # the two starters
    t = approved(store, "busy")
    store.claim("housekeeper")
    assert f"housekeeper on #{t['id']} (0 min)" in console.summary(store)
    store.release(t["id"], PERSON, blocked="stopped by the person")
    assert "1 blocked" in console.summary(store)


def test_status_names_a_stopped_runner(store, monkeypatch):
    monkeypatch.setattr(console, "_runner", lambda role: "inactive" if role == "housekeeper" else "")
    assert "housekeeper idle (runner inactive) · update-watcher idle ·" in console.summary(store)


def test_next_run_counts_down_once_the_series_is_closed(store, monkeypatch):
    monkeypatch.setattr(console, "_runner", lambda role: "")
    root = store.create("daily", "true", ["updates"], actor=PERSON, schedule="1d")
    store.approve(root["id"], PERSON)
    store.claim("update-watcher", root["id"])
    store.finish(root["id"], "update-watcher")
    store.verify(root["id"], "runner:update-watcher")
    assert console.summary(store, now=time.time() + 3600).endswith("next recurring run in 23 h")


def test_console_runs_claude_in_its_folder_as_the_person(store, monkeypatch):
    monkeypatch.setenv("ODD1_TASKS_AGENT", "housekeeper")  # even if started from a role's environment
    seen = {}
    monkeypatch.setattr(console.os, "execvpe", lambda file, args, env: seen.update(args=args, env=env))
    monkeypatch.chdir(store.state)
    console.run(store)
    folder = store.state / "console"
    assert (folder / "CLAUDE.md").exists()
    assert seen["args"][:4] == ["claude", "-n", "Task manager", "--settings"]
    assert seen["args"][4] == str(folder / "settings.json") and "What is the task manager doing" in seen["args"][5]
    assert "ODD1_TASKS_AGENT" not in seen["env"]
    settings = json.loads((folder / "settings.json").read_text())
    assert settings["statusLine"]["command"] == "odd1-tasks status"
    allowed = settings["permissions"]["allow"]
    assert "mcp__tasks__tasks_approve" not in allowed  # approving always asks the person
    assert not any("release" in a for a in allowed)  # so does stopping a task
    asked = settings["permissions"]["ask"]  # and they ask even in auto mode, which may otherwise approve on its own
    assert {"mcp__tasks__tasks_approve", "mcp__tasks__tasks_release", "Bash(odd1-tasks approve:*)"} <= set(asked)


def test_status_says_a_role_is_paused(store, monkeypatch):
    monkeypatch.setattr(console, "_runner", lambda role: "")
    path = store.state / "roles" / "housekeeper" / "role.toml"
    path.write_text(path.read_text().replace("enabled = true", "enabled = false"))
    assert "housekeeper idle (paused)" in console.summary(store)
