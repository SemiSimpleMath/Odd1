"""Ask the person, on their own screen, before an agent takes control of one of their windows, and let them take it
back at any time.

A small dialog above everything, below the top bar: "Claude wants to maximize "Mousepad"" with the agent's reason,
[Deny] [Allow for 5 minutes]. It opens without taking the keyboard focus (the person may be typing elsewhere); a
click answers it, and no answer in two minutes is a no. A yes turns the dialog into a bar, "Claude is controlling
"Mousepad" 4:59 [Take back]", and the grant is that bar: the agent may act on the window only while it is open. It
closes when the person takes control back, when the minutes run out, or when the agent's MCP server exits. After a
take-back the agent may not ask about that window again for a while; the person can still say yes in the chat.

    python -m odd1_screen.consent WHO WHAT TITLE [REASON] [SLOT]   the dialog: prints "allowed" on a yes and stays
        as the bar; exit 1 denied, 2 no answer, 0 the minutes ran out, 3 taken back
"""
import os
import select
import subprocess
import sys
import time

from . import x11

GRANT_MINUTES = 5
ANSWER_SECONDS = 120
DENIED, NO_ANSWER, EXPIRED, TAKEN_BACK = 1, 2, 0, 3


class Grants:
    """Windows the person let this agent control: xid -> the bar showing it (a process; open = held), and
    windows they took back: xid -> until when the agent may not ask again."""

    def __init__(self):
        self.bars, self.refused_until = {}, {}

    def held(self, xid: str) -> bool:
        bar = self.bars.get(xid)
        if bar is None:
            return False
        if bar.poll() is None:
            return True
        del self.bars[xid]
        if bar.returncode == TAKEN_BACK:
            self.refused_until[xid] = time.time() + GRANT_MINUTES * 60
        return False

    def refused(self, xid: str, now: float) -> bool:
        return self.refused_until.get(xid, 0) > now

    def live(self) -> int:
        return sum(self.held(xid) for xid in list(self.bars))


GRANTS = Grants()


def who() -> str:
    """How the dialog names the asking agent: its task role when a runner started it, else Claude."""
    role = os.environ.get("ODD1_TASKS_AGENT")
    return f"Claude ({role})" if role else "Claude"


def ask(display: str | None, what: str, title: str, reason: str = "", slot: int = 0):
    """Show the dialog on `display` and wait for the person: (0, bar) allowed, the bar process holding the grant;
    (1, None) denied; (2, None) no answer."""
    bar = subprocess.Popen([sys.executable, "-m", "odd1_screen.consent", who(), what, title, reason, str(slot)],
                           env=x11.env(display), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    ready, _, _ = select.select([bar.stdout], [], [], ANSWER_SECONDS + 10)
    if ready and bar.stdout.readline().strip() == "allowed":
        return 0, bar
    try:
        code = bar.wait(timeout=5)
    except subprocess.TimeoutExpired:
        bar.kill()
        code = NO_ANSWER
    return (code if code in (DENIED, NO_ANSWER) else NO_ANSWER), None


def require(display: str | None, xid: str, title: str, what: str, reason: str = "", *, grants: Grants = GRANTS,
            asker=ask, now=time.time) -> str:
    """Return "granted" (the person's bar for this window is open) or "allowed" (they said yes just now), else
    raise PermissionError."""
    if grants.held(xid):
        return "granted"
    if grants.refused(xid, now()):
        raise PermissionError(f"the person took back control of {title!r}: do not ask again for it now; tell them "
                              "in the chat what you need, and act only if they say yes there (person_approved).")
    answer, bar = asker(display, what, title, reason, grants.live())
    if answer == 0:
        grants.bars[xid] = bar
        return "allowed"
    if answer == DENIED:
        raise PermissionError(f"the person declined: {what} {title!r}. Do not ask again for this; find another way "
                              "or tell them in the chat.")
    raise PermissionError(f"the person did not answer within {ANSWER_SECONDS}s (they may be away): {what} {title!r}")


def still_held(xid: str, grants: Grants = GRANTS) -> bool:
    """For long actions (typing): whether a grant given by the dialog is still open; True when none was needed."""
    return xid not in grants.bars or grants.held(xid)


CSS = b"""
window { background: #1b1d2a; color: #e4e4e8; border: 1px solid #3a3d52; }
.ask { font-size: 15px; font-weight: bold; }
.why, .left { color: #a8aab8; }
.dot { color: #d97757; }
button { padding: 6px 14px; }
.allow { background: #d97757; color: #ffffff; }
.back { background: #3a3d52; color: #ffffff; font-weight: bold; }
"""


def dialog(who_name: str, what: str, title: str, reason: str, slot: int = 0) -> int:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk, Gdk

    state = {"code": NO_ANSWER, "until": 0.0}
    parent = os.getppid()
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

    def label(text, style, width=60):
        widget = Gtk.Label(label=text, xalign=0)
        widget.set_line_wrap(True)
        widget.set_max_width_chars(width)
        widget.get_style_context().add_class(style)
        return widget

    def button(text, style, on_click):
        widget = Gtk.Button(label=text)
        widget.set_can_focus(False)  # never a default button a stray Enter could press
        if style:
            widget.get_style_context().add_class(style)
        widget.connect("clicked", lambda _b: on_click())
        return widget

    def place():
        width = window.get_preferred_width()[1]
        monitor = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
        area = monitor.get_workarea()
        window.move(area.x + (area.width - width) // 2, area.y + 16 + slot * 56)

    def finish(code):
        state["code"] = code
        Gtk.main_quit()

    def tick():
        if os.getppid() != parent:  # the agent's server is gone: so is its grant
            finish(EXPIRED)
            return False
        if state["until"]:
            left = state["until"] - time.time()
            if left <= 0:
                finish(EXPIRED)
                return False
            remaining.set_text(f"{int(left) // 60}:{int(left) % 60:02d} left")
        return True

    def allow():
        print("allowed", flush=True)
        state["until"] = time.time() + GRANT_MINUTES * 60
        window.remove(box)
        window.set_title("Odd1: an agent is controlling a window")
        window.add(bar)
        bar.show_all()
        window.resize(1, 1)
        place()

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=18)
    box.pack_start(label(f"{who_name} wants to {what} “{title}”", "ask"), False, False, 0)
    if reason:
        box.pack_start(label(reason, "why"), False, False, 0)
    buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
    buttons.pack_start(button("Deny", None, lambda: finish(DENIED)), False, False, 0)
    buttons.pack_start(button(f"Allow for {GRANT_MINUTES} minutes", "allow", allow), False, False, 0)
    box.pack_start(buttons, False, False, 0)

    bar = Gtk.Box(spacing=10, margin=8)
    bar.pack_start(label("●", "dot", 2), False, False, 0)
    bar.pack_start(label(f"{who_name} is controlling “{title}”", "ask", 50), False, False, 0)
    remaining = label("", "left", 10)
    bar.pack_start(remaining, False, False, 0)
    bar.pack_start(button("Take back", "back", lambda: finish(TAKEN_BACK)), False, False, 0)

    window.add(box)
    window.connect("delete-event", lambda *_: finish(TAKEN_BACK if state["until"] else DENIED) or True)
    window.show_all()
    place()
    Gdk.beep()
    GLib.timeout_add_seconds(ANSWER_SECONDS, lambda: (not state["until"] and finish(NO_ANSWER)) and False)
    GLib.timeout_add(500, tick)
    Gtk.main()
    return state["code"]


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__.strip().splitlines()[-2].strip(), file=sys.stderr)
        raise SystemExit(NO_ANSWER)
    args = sys.argv[1:] + ["", "0"][len(sys.argv) - 4:]
    raise SystemExit(dialog(args[0], args[1], args[2], args[3], int(args[4] or 0)))
