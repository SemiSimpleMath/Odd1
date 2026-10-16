"""Ask the person, on their own screen, before an agent takes control of one of their windows.

A small dialog above everything, below the top bar: "Claude wants to maximize "Mousepad"" with the agent's reason,
[Deny] [Allow for 5 minutes]. It opens without taking the keyboard focus (the person may be typing elsewhere); a
click answers it, and no answer in two minutes is a no. A yes is a grant for that window, held in this process
(one agent session's MCP server) for the minutes shown, so a few steps on one window ask once.

    python -m odd1_screen.consent WHO WHAT TITLE [REASON]   exit 0 allowed, 1 denied, 2 no answer (the dialog)
"""
import os
import subprocess
import sys
import time

from . import x11

GRANT_MINUTES = 5
ANSWER_SECONDS = 120


class Grants:
    """Windows (xid -> expiry) the person let this agent control, until the expiry."""

    def __init__(self):
        self.until = {}

    def held(self, xid: str, now: float) -> bool:
        return self.until.get(xid, 0) > now

    def grant(self, xid: str, now: float, minutes: float = GRANT_MINUTES) -> None:
        self.until[xid] = now + minutes * 60


GRANTS = Grants()


def who() -> str:
    """How the dialog names the asking agent: its task role when a runner started it, else Claude."""
    role = os.environ.get("ODD1_TASKS_AGENT")
    return f"Claude ({role})" if role else "Claude"


def ask(display: str | None, what: str, title: str, reason: str = "") -> int:
    """Show the dialog on `display` and wait for the person: 0 allowed, 1 denied, 2 no answer."""
    try:
        done = subprocess.run([sys.executable, "-m", "odd1_screen.consent", who(), what, title, reason],
                              timeout=ANSWER_SECONDS + 10, env=x11.env(display), capture_output=True)
    except subprocess.TimeoutExpired:
        return 2
    return done.returncode if done.returncode in (0, 1) else 2


def require(display: str | None, xid: str, title: str, what: str, reason: str = "", *, grants: Grants = GRANTS,
            asker=ask, now=time.time) -> str:
    """Return "granted" (held already) or "allowed" (the person said yes just now), else raise PermissionError."""
    if grants.held(xid, now()):
        return "granted"
    answer = asker(display, what, title, reason)
    if answer == 0:
        grants.grant(xid, now())
        return "allowed"
    if answer == 1:
        raise PermissionError(f"the person declined: {what} {title!r}. Do not ask again for this; find another way "
                              "or tell them in the chat.")
    raise PermissionError(f"the person did not answer within {ANSWER_SECONDS}s (they may be away): {what} {title!r}")


CSS = b"""
window { background: #1b1d2a; color: #e4e4e8; border: 1px solid #3a3d52; }
.ask { font-size: 15px; font-weight: bold; }
.why { color: #a8aab8; }
button { padding: 6px 14px; }
.allow { background: #d97757; color: #ffffff; }
"""


def dialog(who_name: str, what: str, title: str, reason: str) -> int:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk, Gdk

    answer = {"code": 2}
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    window = Gtk.Window(title="Odd1: an agent asks")
    window.set_keep_above(True)
    window.set_focus_on_map(False)  # the person may be typing elsewhere: a click answers, keys stay theirs
    window.set_type_hint(Gdk.WindowTypeHint.DIALOG)
    window.set_skip_taskbar_hint(True)
    window.set_resizable(False)
    window.stick()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=18)
    ask_label = Gtk.Label(label=f"{who_name} wants to {what} “{title}”", xalign=0)
    ask_label.set_line_wrap(True)
    ask_label.set_max_width_chars(60)
    ask_label.get_style_context().add_class("ask")
    box.pack_start(ask_label, False, False, 0)
    if reason:
        why = Gtk.Label(label=reason, xalign=0)
        why.set_line_wrap(True)
        why.set_max_width_chars(60)
        why.get_style_context().add_class("why")
        box.pack_start(why, False, False, 0)
    buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
    for text, code, style in (("Deny", 1, None), (f"Allow for {GRANT_MINUTES} minutes", 0, "allow")):
        button = Gtk.Button(label=text)
        button.set_can_focus(False)  # never a default button a stray Enter could press
        if style:
            button.get_style_context().add_class(style)
        button.connect("clicked", lambda _b, c=code: (answer.update(code=c), Gtk.main_quit()))
        buttons.pack_start(button, False, False, 0)
    box.pack_start(buttons, False, False, 0)
    window.add(box)
    window.connect("delete-event", lambda *_: Gtk.main_quit())
    window.show_all()
    width = window.get_preferred_width()[1]
    monitor = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
    area = monitor.get_workarea()
    window.move(area.x + (area.width - width) // 2, area.y + 16)
    Gdk.beep()
    GLib.timeout_add_seconds(ANSWER_SECONDS, Gtk.main_quit)
    Gtk.main()
    return answer["code"]


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(dialog(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else ""))
