"""Window screenshots: picking the window, cropping the frame, and (live) capturing a covered window."""
import os
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


def live_display():
    """The X display for live fixtures: the agent desktop when it runs, so tests never put windows on the person's
    screen; else $DISPLAY; None without either."""
    agent = screenshot.resolve_display("agent")
    if os.path.exists(f"/tmp/.X11-unix/X{agent.lstrip(':').split('.')[0]}"):
        return agent
    return os.environ.get("DISPLAY") or None


def live_ready():
    try:
        import PIL, Xlib, tkinter  # noqa: F401
    except ImportError:
        return False
    return live_display() is not None


# A Tk window on DISPLAY; with a 4th argument it is override-redirect (no window manager, so it maps on top of
# everything without being activated). Prints "ready" once it is mapped.
TK_WINDOW = """
import sys, tkinter
root = tkinter.Tk(); root.title(sys.argv[1]); root.geometry(sys.argv[3])
root.overrideredirect(len(sys.argv) > 4)
tkinter.Frame(root, bg=sys.argv[2], width=300, height=200).pack(fill="both", expand=True)
root.wait_visibility(); root.update(); print("ready", flush=True)
root.mainloop()
"""


def tk_window(display, *args):
    window = subprocess.Popen([sys.executable, "-c", TK_WINDOW, *args], stdout=subprocess.PIPE, text=True,
                              env={**os.environ, "DISPLAY": display})
    assert window.stdout.readline().strip() == "ready", "the Tk fixture window did not map"
    return window


def capture_when_listed(query, out, display, timeout=10):
    """screenshot.capture, retried until the window manager lists the new window."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return screenshot.capture(query, out, display_name=display)
        except LookupError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


@pytest.mark.skipif(not live_ready(), reason="needs an X display, python-xlib, Pillow and tkinter")
def test_live_capture_of_a_covered_window(tmp_path):
    from PIL import Image
    display = live_display()
    tag = f"odd1-shot-{os.getpid()}"
    windows = [tk_window(display, f"{tag}-below", "#ff0000", "300x200+300+300")]
    try:
        windows.append(tk_window(display, f"{tag}-above", "#0000ff", "500x400+200+200", "override-redirect"))
        shot = capture_when_listed(f"{tag}-below", tmp_path / "below.png", display)
        picture = Image.open(shot["path"]).convert("RGB")
        assert (shot["width"], shot["height"]) == (300, 200)
        assert picture.getpixel((150, 100)) == (255, 0, 0), "the covered window's own pixels, not the cover's"
    finally:
        for window in windows:
            window.kill()


def test_main_takes_a_window_or_the_screen(capsys):
    for argv in ([], ["calc", "--screen"], ["--screen", "--frame"]):
        with pytest.raises(SystemExit):
            screenshot.main(argv)
        assert "give a WINDOW" in capsys.readouterr().err


@pytest.mark.skipif(not live_ready(), reason="needs an X display, python-xlib, Pillow and tkinter")
def test_live_capture_of_the_whole_screen(tmp_path):
    from PIL import Image
    from Xlib import display as xdisplay
    display = live_display()
    connection = xdisplay.Display(display)
    geometry = connection.screen().root.get_geometry()
    connection.close()
    window = tk_window(display, f"odd1-screen-{os.getpid()}", "#00ff00", "300x200+100+100")
    try:
        time.sleep(screenshot.SETTLE_SECONDS)
        shot = screenshot.capture_screen(tmp_path / "screen.png", display_name=display)
    finally:
        window.kill()
    picture = Image.open(shot["path"]).convert("RGB")
    assert shot["window"] == "root" and picture.size == (geometry.width, geometry.height)
    assert (0, 255, 0) in {colour for _, colour in picture.getcolors(1 << 24)}, "the open window is in the grab"
