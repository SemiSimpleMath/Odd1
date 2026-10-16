"""Make odd1_tasks importable; give every test its own state directory with both roles seeded and a stub claude."""
import sys
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COMPONENT))

from odd1_tasks import roles, runner  # noqa: E402
from odd1_tasks.store import Store  # noqa: E402

STUB = Path(__file__).resolve().parent / "stub_claude.py"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ODD1_TASKS_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("PYTHONPATH", str(COMPONENT))
    monkeypatch.setenv("USER", "tester")
    monkeypatch.delenv("ODD1_TASKS_AGENT", raising=False)
    monkeypatch.setenv("STUB_RECORD", str(tmp_path / "stub.jsonl"))
    monkeypatch.setattr(runner, "CLAUDE", [sys.executable, str(STUB)])
    s = Store()
    for role in ("housekeeper", "update-watcher"):
        roles.seed(s, role)
    return s


PERSON = "person:tester"


def approved(store, goal="g", done_when="true", tags=("housekeeping",), **kw):
    t = store.create(goal, done_when, tags, actor=PERSON, **kw)
    return store.approve(t["id"], PERSON)
