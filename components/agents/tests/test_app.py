"""The window's application id: one instance per display, never one shared by every desktop."""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio  # noqa: E402

from odd1_agents import app  # noqa: E402


def test_one_instance_per_display(monkeypatch):
    ids = {d: app.app_id(d) for d in (":0", ":1", ":1.0", "localhost:10.0", "")}
    assert len(set(ids.values())) == len(ids)
    assert all(Gio.Application.id_is_valid(i) for i in ids.values()), ids
    monkeypatch.setenv("DISPLAY", ":1")
    assert app.App().get_application_id() == ids[":1"] != ids[":0"]
