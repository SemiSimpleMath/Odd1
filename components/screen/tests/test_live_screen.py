"""Live check on the Odd1 Desktop: a disposable Tk window (no accessibility tree) is found, read by OCR where it is
on top, and owns its pixels. Skipped without a display and the tools."""
import os
import shutil
import subprocess
import sys
import time

import pytest

from odd1_screen import live, model


def _missing():
    if not os.environ.get("DISPLAY"):
        return "needs the Odd1 Desktop (DISPLAY unset)"
    absent = [t for t in ("tesseract", "import", "xprop", "xwininfo", "wmctrl") if shutil.which(t) is None]
    if absent:
        return "needs " + ", ".join(absent)
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return "needs tkinter"
    return None


FIXTURE = """
import tkinter as tk
root = tk.Tk(); root.title("odd1 screen probe"); root.geometry("420x120+60+60")
tk.Label(root, text="Quarterly totals ready", font=("DejaVu Sans", 20), bg="white").pack(expand=True, fill="both")
root.after(30000, root.destroy); root.mainloop()
"""


@pytest.mark.skipif(_missing() is not None, reason=_missing() or "")
def test_tk_window_is_read_by_ocr_and_owns_its_pixels():
    probe = subprocess.Popen([sys.executable, "-c", FIXTURE])
    try:
        for _ in range(50):
            if "odd1 screen probe" in subprocess.run(["wmctrl", "-l"], capture_output=True, text=True).stdout:
                break
            time.sleep(0.2)
        subprocess.run(["wmctrl", "-a", "odd1 screen probe"], check=False)
        time.sleep(0.5)
        screen = model.Screen()
        view = live.observe(screen, window="odd1 screen probe", ocr="always")
        window = view["windows"][0]
        assert window["source"] == "ocr"
        line = next(e for e in window["elements"] if "totals" in e["name"].casefold())
        assert screen.at(*model.centre(line["box"]))["window"]["id"] == window["id"]
    finally:
        probe.kill()
