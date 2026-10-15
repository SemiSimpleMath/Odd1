"""Live: a program started on the agent desktop gets a window there, not on the person's screen, and the person's
pointer does not move when the agent desktop's does. Skipped unless the agent desktop is running."""
import os
import subprocess
import sys

import pytest

from odd1_agent_desktop import desktop


def _missing():
    try:
        return None if desktop.running() else "the agent desktop is not running"
    except (OSError, subprocess.SubprocessError):
        return "needs xdpyinfo"


@pytest.mark.skipif(_missing() is not None, reason=_missing() or "")
def test_app_runs_on_the_agent_desktop_without_touching_the_person():
    env_user = {**os.environ, "DISPLAY": desktop.USER_DISPLAY}
    env_agent = {**os.environ, "DISPLAY": desktop.DISPLAY}
    pointer = lambda env: subprocess.run(["xdotool", "getmouselocation"], env=env, capture_output=True,
                                         text=True).stdout.split(" window")[0]
    before = pointer(env_user)
    probe = 'import tkinter as tk; r = tk.Tk(); r.title("odd1 agent desktop probe"); r.after(20000, r.destroy); r.mainloop()'
    started = desktop.run([sys.executable, "-c", probe])
    try:
        assert [w["title"] for w in started["windows"]] == ["odd1 agent desktop probe"]
        user_titles = [w["title"] for w in desktop.parse_wmctrl(desktop._x(["wmctrl", "-l", "-p"], desktop.USER_DISPLAY))]
        assert not any("odd1 agent desktop probe" in t for t in user_titles)
        subprocess.run(["xdotool", "mousemove", "--sync", "17", "23"], env=env_agent, check=True)
        assert pointer(env_user) == before
    finally:
        desktop.stop(started["unit"])
