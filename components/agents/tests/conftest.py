"""Make odd1_agents and odd1_tasks importable; each test gets a task store with both roles seeded, and an empty
caretaker and registry, so nothing reads the machine's own state."""
import sys
from pathlib import Path

import pytest

COMPONENTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(COMPONENTS / "agents"))
sys.path.insert(0, str(COMPONENTS / "tasks"))

from odd1_agents import model  # noqa: E402
from odd1_tasks import console, roles  # noqa: E402
from odd1_tasks.store import Store  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ODD1_TASKS_STATE", str(tmp_path / "tasks"))
    monkeypatch.setenv("USER", "tester")
    monkeypatch.delenv("ODD1_TASKS_AGENT", raising=False)
    monkeypatch.setattr(model, "CARETAKER", tmp_path / "caretaker")
    monkeypatch.setattr(model, "AGENTS", tmp_path / "agents.json")
    monkeypatch.setattr(model, "MCP_SERVERS", tmp_path / "mcp-servers.json")
    monkeypatch.setattr(console, "_runner", lambda role: "")
    s = Store()
    for role in ("housekeeper", "update-watcher"):
        roles.seed(s, role)
    return s
