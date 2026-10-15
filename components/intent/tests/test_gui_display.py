"""Real Tk smoke test: the Intent window builds on a live display and closes. Skips without one."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

if not sys.platform.startswith("linux"):
    pytest.skip("Intent's engine is Linux-only (fcntl.flock, renameat2, /proc/self/fd); run on Odd1 or another Linux host",
                allow_module_level=True)
if not os.environ.get("DISPLAY"):
    pytest.skip("no X display (DISPLAY unset); on Odd1 run with DISPLAY=:0", allow_module_level=True)
tk = pytest.importorskip("tkinter", reason="tkinter is not installed")


def test_window_builds_with_disabled_approval(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="odd1-intent-gui-") as td:
        monkeypatch.setattr("odd1_intent.engine.ROOT", Path(td) / "state")
        monkeypatch.setattr("odd1_intent.engine.TXROOT", Path(td) / "state/transactions")
        monkeypatch.setattr("odd1_intent.engine.LOG", Path(td) / "state/history.json")
        from odd1_intent import gui
        try:
            app = gui.App()
        except tk.TclError as error:
            pytest.skip(f"display {os.environ['DISPLAY']} is not usable: {error}")
        try:
            app.update_idletasks()
            assert str(app.approve.cget("state")) == "disabled"
            assert app.plan is None
        finally:
            app.destroy()
