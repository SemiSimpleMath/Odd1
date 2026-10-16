"""Make odd1_tasks importable; give every test its own state directory with both roles seeded and a stub claude."""
import os
import sys
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COMPONENT))

# Test this checkout's seeds, not the deployed ones a runner's environment names (a routine or role new on a branch
# would otherwise be missing): drop each seed variable whose directory this checkout has, before odd1_tasks reads it.
# The repository's routines live at its top level, like the wiki.
for var, path in (("ODD1_TASKS_ROUTINES_SEED", COMPONENT.parents[1] / "routines"),
                  ("ODD1_TASKS_ROLES_SEED", COMPONENT / "roles"),
                  ("ODD1_TASKS_CONSOLE_SEED", COMPONENT / "console")):
    if path.is_dir():
        os.environ.pop(var, None)

from odd1_tasks import roles, runner  # noqa: E402
from odd1_tasks.store import Store  # noqa: E402

STUB = Path(__file__).resolve().parent / "stub_claude.py"
# This checkout's routines when there is one, else the installed seed.
ROUTINES = COMPONENT.parents[1] / "routines"
if not ROUTINES.is_dir():
    ROUTINES = Path(os.environ.get("ODD1_TASKS_ROUTINES_SEED", ROUTINES))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ODD1_TASKS_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("PYTHONPATH", str(COMPONENT))
    monkeypatch.setenv("USER", "tester")
    monkeypatch.delenv("ODD1_TASKS_AGENT", raising=False)
    monkeypatch.setenv("STUB_RECORD", str(tmp_path / "stub.jsonl"))
    monkeypatch.setattr(runner, "CLAUDE", [sys.executable, str(STUB)])
    from odd1_tasks import workspace
    monkeypatch.setattr(workspace, "REPO", tmp_path / "no-repo")  # never the real checkout, whatever a test does
    s = Store()
    for role in ("housekeeper", "update-watcher"):
        roles.seed(s, role)
    return s


PERSON = "person:tester"


def approved(store, goal="g", done_when="true", tags=("housekeeping",), **kw):
    t = store.create(goal, done_when, tags, actor=PERSON, **kw)
    return store.approve(t["id"], PERSON)
