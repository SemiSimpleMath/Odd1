"""The cron manager: routine folders become recurring tasks, a worker runs each handed its routine's skill and
permissions; and the journal routine's material script."""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from conftest import COMPONENT, PERSON

from odd1_tasks import cron, roles, runner

ROUTINE = """enabled = {enabled}
schedule = "1d"
goal = "{goal}"
done_when = "test -s out/today.md"
max_turns = 9
allow = ["Write(./out/**)", "Bash(python3 gather.py)"]
"""


def make(source: Path, name="notes", enabled="true", goal="Today's notes are written."):
    (source / name).mkdir(parents=True, exist_ok=True)
    (source / name / "routine.toml").write_text(ROUTINE.format(enabled=enabled, goal=goal))
    (source / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: write notes\n---\nWrite them.\n")
    (source / name / "out").mkdir(exist_ok=True)
    (source / name / "out" / ".keep").write_text("")


def routine_tasks(store):
    return [t for t in store.list() if t["created_by"] == "cron"]


def test_a_routine_becomes_one_recurring_proposal(store, tmp_path):
    source = tmp_path / "routines"
    make(source)
    assert cron.sync(store, source) == [f"notes: proposed #{routine_tasks(store)[0]['id']} (every 1d)"]
    assert cron.sync(store, source) == []  # once
    [t] = routine_tasks(store)
    assert t["status"] == "proposed" and t["schedule"] == "1d" and t["tags"] == ["routine", "routine-notes"]
    assert t["max_turns"] == 9 and (cron.folder(store) / "notes" / "SKILL.md").exists()
    assert cron.listing(store)[0] | {"folder": ""} == {"name": "notes", "enabled": True, "schedule": "1d",
                                                       "goal": "Today's notes are written.", "task": t["id"],
                                                       "status": "proposed", "folder": ""}


def test_switched_off_changed_and_removed_routines_end_their_series(store, tmp_path):
    source = tmp_path / "routines"
    make(source)
    cron.sync(store, source)
    [first] = routine_tasks(store)
    store.approve(first["id"], PERSON)
    (cron.folder(store) / "notes" / "routine.toml").write_text(ROUTINE.format(enabled="true", goal="Better notes."))
    cron.sync(store, source)  # the person's edit stays (seeding keeps it) and replaces the series
    assert store.show(first["id"])["status"] == "cancelled"
    [second] = [t for t in routine_tasks(store) if t["status"] == "proposed"]
    assert second["goal"] == "Better notes."
    (cron.folder(store) / "notes" / "routine.toml").write_text(ROUTINE.format(enabled="false", goal="Better notes."))
    cron.sync(store, source)
    assert store.show(second["id"])["status"] == "declined"
    make(source, "other")
    cron.sync(store, source)
    [other] = [t for t in routine_tasks(store) if "routine-other" in t["tags"]]
    shutil.rmtree(cron.folder(store) / "other")
    shutil.rmtree(source / "other")
    assert cron.sync(store, source) == [f"other: removed, ended #{other['id']}"]


def test_the_manager_decides_routines(store, tmp_path):
    roles.seed(store, "manager")
    source = tmp_path / "routines"
    make(source)
    cron.sync(store, source)
    [t] = routine_tasks(store)
    assert store.may_decide("manager", t) and not store.may_decide("housekeeper", t)


def test_a_worker_runs_a_routine_handed_its_skill_and_permissions(store, tmp_path, monkeypatch):
    source = tmp_path / "routines"
    make(source)
    cron.sync(store, source)
    [t] = routine_tasks(store)
    store.approve(t["id"], PERSON)
    roles.seed(store, "worker")
    assert {"routine", "routine-notes"} <= set(roles.load(store, "worker")["tags"])
    monkeypatch.setenv("STUB_PLAN", json.dumps({"touch": ["out/today.md"], "finish": True}))
    done = runner.run_once(store, "worker")
    assert done["id"] == t["id"] and done["status"] == "done"  # done_when ran in the routine's folder
    [call] = [json.loads(line) for line in open(os.environ["STUB_RECORD"])]
    folder = cron.folder(store) / "notes"
    assert call["cwd"] == str(folder) and "the `notes` skill" in call["prompt"]
    assert (folder / ".claude/skills/notes/SKILL.md").read_text().startswith("---\nname: notes")
    settings = json.loads(Path(call["argv"][call["argv"].index("--settings") + 1]).read_text())
    allow = settings["permissions"]["allow"]
    assert "Write(./out/**)" in allow and "Skill" in allow and "mcp__tasks__tasks_approve" not in allow
    assert str(roles.folder(store, "worker") / "CLAUDE.md") in call["argv"]


def test_the_journal_routine_loads(store):
    cron.seed(store)
    r = cron.load(store, "journal")
    assert r["enabled"] and r["schedule"] == "1d" and "Write(./journal/**)" in r["allow"]
    assert (r["folder"] / "SKILL.md").read_text().startswith("---\nname: journal\n")


# -- journal-sources.py -------------------------------------------------------------------------------------------
SOURCES = COMPONENT / "routines" / "journal" / "journal-sources.py"


def transcript(folder: Path, lines):
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "s.jsonl", "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def stamp(dt):
    return dt.astimezone().isoformat()


def test_journal_sources_gather_the_window_and_leave_out_what_is_not_the_persons(store, tmp_path):
    now = datetime.now()
    t = store.create("Deploys ship only committed work", "true", ["friction"], actor=PERSON)
    store.approve(t["id"], PERSON)
    store.log(t["id"], PERSON, "The person chose option 1")
    with store._tx():
        store._log(t["id"], "fixer", "note", "agent working note")
    projects = tmp_path / "projects"
    transcript(projects / "-srv-odd1", [
        {"type": "ai-title", "aiTitle": "deploy work"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Should I go with option 1?"}]}},
        {"type": "user", "timestamp": stamp(now - timedelta(minutes=5)), "message": {"content": "Yes approve option 1"}},
        {"type": "user", "timestamp": stamp(now - timedelta(minutes=4)),
         "message": {"content": [{"type": "tool_result", "content": "tool output"}]}},
        {"type": "user", "timestamp": stamp(now - timedelta(minutes=3)), "message": {"content": "<system-reminder>x"}},
        {"type": "user", "timestamp": stamp(now - timedelta(days=3)), "message": {"content": "an old message"}},
    ])
    transcript(projects / "-var-lib-odd1-tasks-roles-fixer", [
        {"type": "user", "timestamp": stamp(now), "message": {"content": "a role's own prompt"}}])
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "-m", "deploy: odd1-deploy"], check=True)
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "deployments.jsonl").write_text(json.dumps(
        {"at": now.timestamp() - 60, "commit": "a" * 40, "action": "switch", "ok": True, "actor": PERSON,
         "reason": "first"}) + "\n")
    env = dict(os.environ, ODD1_JOURNAL_PROJECTS=str(projects), ODD1_REPO=str(repo), ODD1_DEPLOY_STATE=str(deploy),
               ODD1_JOURNAL_DIR=str(tmp_path / "journal"))
    out = subprocess.run([sys.executable, str(SOURCES), "--since", (now - timedelta(hours=1)).isoformat()],
                         env=env, capture_output=True, text=True, check=True).stdout
    assert "Yes approve option 1" in out and "Should I go with option 1?" in out and "deploy work" in out
    assert f"#{t['id']} approve by {PERSON}" in out and "The person chose option 1" in out
    assert "deploy: odd1-deploy" in out and "aaaaaaaaaaaa switch ok" in out
    for absent in ("tool output", "system-reminder", "an old message", "a role's own prompt", "agent working note"):
        assert absent not in out
