"""Window screenshots: picking the window, cropping the frame, and (live) capturing a covered window."""
import os
import shutil
import subprocess
import sys
import time

import pytest

from any_app import screenshot

CLIENTS = [(0x1400004, "Calculator", "org.gnome.Calculator"), (0x1600007, "Untitled", "FeatherPad"),
           (0x1a00003, "Claude Code", "Xfce4-terminal"), (0xe00003, "Claude Code", "Xfce4-terminal")]
CALC, PAD = CLIENTS[0], CLIENTS[1]


def test_match_window_by_title_or_id():
    assert screenshot.match_window(CLIENTS, "calc") == CALC
    assert screenshot.match_window(CLIENTS, "featherpad") == PAD  # by app when the title says nothing
    assert screenshot.match_window(CLIENTS, "0x1600007") == PAD
    assert screenshot.match_window(CLIENTS, str(0x1400004)) == CALC
    assert screenshot.match_window(CLIENTS, "0x99") == (0x99, "", "")  # unmanaged windows are allowed by id


def test_match_window_fails_closed_and_lists_windows():
    with pytest.raises(LookupError, match="2 windows match 'claude'.*Calculator"):
        screenshot.match_window(CLIENTS, "claude")
    with pytest.raises(LookupError, match="0 windows match"):
        screenshot.match_window(CLIENTS, "gimp")


def test_client_box_crops_the_frame_unless_asked():
    assert screenshot.client_box((1, 20), (700, 500), (702, 525), frame=False) == (1, 20, 701, 520)
    assert screenshot.client_box((1, 20), (700, 500), (702, 525), frame=True) == (0, 0, 702, 525)


def test_resolve_display_names(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("ODD1_AGENT_DISPLAY", raising=False)
    assert [screenshot.resolve_display(n) for n in ("", "user", "agent", ":5")] == [":0", ":0", ":1", ":5"]
    monkeypatch.setenv("ODD1_AGENT_DISPLAY", ":7")
    assert screenshot.resolve_display("agent") == ":7"
    with pytest.raises(ValueError, match="display must be"):
        screenshot.resolve_display("agent-desktop")


def live_ready():
    try:
        import PIL, Xlib, tkinter  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("DISPLAY")) and shutil.which("xdotool") is not None


TK_WINDOW = """
import sys, tkinter
root = tkinter.Tk(); root.title(sys.argv[1]); root.geometry(sys.argv[3])
tkinter.Frame(root, bg=sys.argv[2], width=300, height=200).pack(fill="both", expand=True)
root.mainloop()
"""


@pytest.mark.skipif(not live_ready(), reason="needs DISPLAY, python-xlib, Pillow, tkinter and xdotool")
def test_live_capture_of_a_covered_window(tmp_path):
    from PIL import Image
    tag = f"odd1-shot-{os.getpid()}"
    below = subprocess.Popen([sys.executable, "-c", TK_WINDOW, f"{tag}-below", "#ff0000", "300x200+300+300"])
    time.sleep(1.5)
    above = subprocess.Popen([sys.executable, "-c", TK_WINDOW, f"{tag}-above", "#0000ff", "500x400+200+200"])
    try:
        time.sleep(1.5)
        subprocess.run(["xdotool", "search", "--name", f"{tag}-above", "windowactivate", "--sync"], timeout=10)
        shot = screenshot.capture(f"{tag}-below", tmp_path / "below.png")
        picture = Image.open(shot["path"]).convert("RGB")
        assert (shot["width"], shot["height"]) == (300, 200)
        assert picture.getpixel((150, 100)) == (255, 0, 0), "the covered window's own pixels, not the cover's"
    finally:
        below.kill()
        above.kill()
