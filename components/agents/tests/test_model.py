"""The Agents app's model: overview, task views and actions, role settings written into role.toml, tools."""
import json
import time
import tomllib

import pytest

from odd1_agents import model
from odd1_tasks import roles


def test_overview_names_each_role_what_waits_and_the_machine(store, tmp_path):
    (tmp_path / "caretaker").mkdir()
    now = time.time()
    (tmp_path / "caretaker" / "health.json").write_text(json.dumps(
        {"ok": True, "last_success_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - 30))}))
    (tmp_path / "caretaker" / "latest.json").write_text(json.dumps({"level": "elevated", "problems": ["disk 91%"]}))
    t = store.create("job", "true", ["housekeeping"], actor="person:tester")
    store.approve(t["id"], "person:tester")
    store.claim("housekeeper")
    store.log(t["id"], "runner:housekeeper", "run ended", kind="run-end", total_cost_usd=0.25)
    view = model.overview(store, now)
    house = next(r for r in view["roles"] if r["name"] == "housekeeper")
    assert house["enabled"] and house["runner"] == "active"
    assert house["doing"]["id"] == t["id"] and house["cost_today"] == 0.25 and house["runs_today"] == 1
    assert view["waiting"] == {"proposed": 2, "blocked": 0, "parked": 0}  # the two starters
    assert view["machine"] == {"state": "current", "level": "elevated", "problems": ["disk 91%"],
                               "age": pytest.approx(30, abs=2)}
    assert view["line"].startswith("Tasks · housekeeper on #")


def test_machine_is_unknown_without_a_caretaker(store):
    assert model.machine()["state"] == "unknown"


def test_task_views_and_the_persons_actions(store):
    t = store.create("Free space", "true", ["housekeeping"], actor="housekeeper")
    waiting = model.tasks(store)
    assert waiting[0]["id"] == t["id"] and waiting[0]["roles"] == ["housekeeper"]  # newest first, who would run it
    model.approve(store, t["id"])
    assert [x["id"] for x in model.tasks(store, "Ready")] == [t["id"]]
    store.claim("housekeeper")
    assert model.stop(store, t["id"])["status"] == "blocked"
    model.note(store, t["id"], "  use the second disk  ")
    model.note(store, t["id"], "   ")  # nothing to say: nothing logged
    log = store.show(t["id"])["log"]
    assert log[-1]["text"] == "use the second disk" and log[-1]["actor"] == "person:tester"
    assert model.approve(store, t["id"])["status"] == "ready"  # unblocked
    assert len(model.tasks(store, "All")) == 3


def test_role_settings_are_written_into_role_toml_keeping_its_comments(store):
    path = roles.folder(store, "housekeeper") / "role.toml"
    before = path.read_text()
    after = model.set_role(store, "housekeeper", enabled=False, runs_per_day=7, model="claude-sonnet-5-5",
                           tags=["housekeeping", "disk"])
    assert (after["enabled"], after["runs_per_day"], after["model"], after["tags"]) == (
        False, 7, "claude-sonnet-5-5", ["housekeeping", "disk"])
    text = path.read_text()
    assert text.count("#") == before.count("#")  # every comment is still there
    assert "runs_per_day = 7          # at most this many runs in any 24 hours" in text
    assert tomllib.loads(text)["starter"] == tomllib.loads(before)["starter"]  # the starter table is untouched
    held = store.db.execute("SELECT tags FROM agents WHERE name = 'housekeeper'").fetchone()[0]
    assert json.loads(held) == ["disk", "housekeeping"]  # the store's claim rule sees the new tags at once


@pytest.mark.parametrize("values", [{"runs_per_day": 1000}, {"max_turns": 0}, {"max_minutes": "10"},
                                    {"enabled": "yes"}, {"tags": []}, {"tags": ["Has Space"]}, {"secret": 1},
                                    {"runs_per_day": True}])
def test_role_settings_refuse_nonsense_and_change_nothing(store, values):
    path = roles.folder(store, "housekeeper") / "role.toml"
    before = path.read_text()
    with pytest.raises(ValueError):
        model.set_role(store, "housekeeper", **values)
    assert path.read_text() == before


def test_a_missing_key_is_added_above_the_tables(store):
    path = roles.folder(store, "housekeeper") / "role.toml"
    path.write_text(path.read_text().replace('model = ""', "").replace("enabled = true", ""))
    model.set_role(store, "housekeeper", model="claude-haiku-5-5", enabled=False)
    data = tomllib.loads(path.read_text())
    assert data["model"] == "claude-haiku-5-5" and data["enabled"] is False and data["starter"]


def test_role_page_points_at_its_files(store):
    folder = roles.folder(store, "update-watcher")
    (folder / "reports" / "2026-10-16.md").write_text("report")
    r = model.role(store, "update-watcher")
    assert r["report"].endswith("2026-10-16.md") and r["instructions"] == str(folder / "CLAUDE.md")
    assert r["tags"] == ["updates"] and r["enabled"] is True


def test_tools_lists_agent_systems_and_mcp_servers(store, tmp_path):
    (tmp_path / "agents.json").write_text(json.dumps({"tasks": {"title": "Task manager"}}))
    (tmp_path / "mcp-servers.json").write_text(json.dumps(
        {"screen": {"command": "/nix/store/x/bin/odd1-screen-mcp", "args": []}}))
    assert model.tools() == {"agents": {"tasks": {"title": "Task manager"}},
                             "mcp_servers": {"screen": "odd1-screen-mcp"}}
