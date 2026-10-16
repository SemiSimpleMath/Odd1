"""A chat window: a terminal (VTE) running an agent system, under a bar of its own.

The bar holds the chat's folder, a gear for this chat's settings and a button that docks the chat in the column or
undocks it into a window of its own. The shell does the docking: the window asks by setting _ODD1_CHAT_DOCK on itself
(1: in the column, 0: a window), and the shell (shell.py) follows the property, so the request survives a restart of
either. A Claude chat starts with a session id of its own, so a change of settings restarts Claude Code on the same
conversation (`--resume`) with the new options; other agent systems get the text size only.

The terminal behaves as the column's xfce4-terminal did: the night palette, JetBrains Mono 12, selecting copies, the
bell marks the window urgent (the tab's orange ball), Ctrl+Shift+C/V copy and paste, Ctrl+click opens a link.
"""
import glob
import os
import shlex
import signal
import subprocess
import sys
import uuid

from .layout import DOCK_PROPERTY
BAR_HEIGHT = 32
FONT = "JetBrains Mono 12"
FOREGROUND, BACKGROUND, CURSOR = "#E6E8F0", "#141827", "#D97757"
SELECTION, SELECTION_BACKGROUND = "#F4F5FA", "#2E3656"
PALETTE = ("#252B42 #E8737C #9FCB84 #E9C27E #6FAEF0 #B58CEB #5CC0C9 #C9CEDC "
           "#5D6787 #F08890 #B2D99A #F0D195 #8CC2F7 #C9A6F2 #79D0D8 #FFFFFF").split()
BAR_CSS = b"""
#odd1-chat-bar { background: #1A1F33; border-bottom: 1px solid #262C44; }
#odd1-chat-bar label { color: #8A8C96; font-family: Inter; font-size: 12px; }
#odd1-chat-bar button { background: none; border: none; box-shadow: none; padding: 2px 6px; min-height: 0;
  min-width: 0; border-radius: 6px; color: #A8ABB8; }
#odd1-chat-bar button:hover { background: #283050; color: #E4E4E8; }
#odd1-chat-settings { padding: 12px 14px; }
#odd1-chat-settings label.heading { font-weight: 600; }
"""
URL = r"(https?|file)://[^\s<>\"'()]*[^\s<>\"'().,;:!?]"
PCRE2_MULTILINE, PCRE2_CASELESS = 0x00000400, 0x00000008

# This chat's Claude Code options: (key, label, choices as (value, label)); None is Claude Code's own default.
MODELS = ((None, "Default"), ("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku"), ("fable", "Fable"))
EFFORTS = ((None, "Default"), ("low", "Low"), ("medium", "Medium"), ("high", "High"), ("xhigh", "Extra high"),
           ("max", "Max"))
MODES = ((None, "Default"), ("auto", "Auto"), ("manual", "Ask first"), ("acceptEdits", "Accept edits"),
         ("plan", "Plan"))
DEFAULTS = {"model": None, "effort": None, "mode": None, "verbose": False}


FLAGS = (("model", "--model"), ("effort", "--effort"), ("mode", "--permission-mode"))


def split_settings(command):
    """A chat command's own arguments, and the settings it already carries (Odd1's Claude starts in auto mode):
    (argv without them, settings)."""
    words, settings, argv = shlex.split(command), dict(DEFAULTS), []
    flags = {flag: key for key, flag in FLAGS}
    index = 0
    while index < len(words):
        word = words[index]
        name, _, value = word.partition("=")
        if name in flags and (value or index + 1 < len(words)):
            settings[flags[name]] = value or words[index + 1]
            index += 1 if value else 2
            continue
        if word == "--verbose":
            settings["verbose"] = True
        else:
            argv.append(word)
        index += 1
    return argv, settings


def claude_argv(command, settings, session, resume=False, prompt=None):
    """The command line for a Claude chat with these settings: `command` (claude and its own arguments, without the
    ones the settings set), then the session (a new one under `session`, or resumed), the model, effort and
    permission mode when chosen, and --verbose when the person wants to see everything Claude does (the code it
    writes, tool output in full). `prompt` opens a new conversation only: a resumed one has had it."""
    argv, _ = split_settings(command)
    argv += ["--resume", session] if resume else ["--session-id", session]
    for key, flag in FLAGS:
        if settings.get(key):
            argv += [flag, settings[key]]
    if settings.get("verbose"):
        argv.append("--verbose")
    if prompt and not resume:
        argv += ["--", prompt]
    return argv


def is_claude(command):
    """Whether a chat's command is Claude Code (its settings apply), not another agent system."""
    words = shlex.split(command or "")
    return bool(words) and os.path.basename(words[0]) == "claude"


def shell_line(argv):
    """The line the chat's bash runs: the agent, then a shell, so the window outlives the agent."""
    return f"{shlex.join(argv)}; exec bash"


def session_saved(session, home=None):
    """Whether Claude Code has a transcript for this session (only then can it be resumed)."""
    home = home or os.path.expanduser("~")
    return bool(glob.glob(os.path.join(home, ".claude", "projects", "*", f"{session}.jsonl")))


def busy(pid):
    """Whether the chat's shell has a program running under it (the agent), from /proc."""
    try:
        for entry in os.listdir("/proc"):
            if entry.isdigit():
                with open(f"/proc/{entry}/stat") as f:
                    if int(f.read().rsplit(")", 1)[1].split()[1]) == pid:
                        return True
    except OSError:
        pass
    return False


class ChatWindow:
    def __init__(self, title, role, cwd, command, docked=True, prompt=None):
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Vte", "2.91")
        from gi.repository import Gdk, GLib, Gtk, Pango, Vte
        self.Gdk, self.GLib, self.Gtk, self.Vte = Gdk, GLib, Gtk, Vte
        self.cwd, self.command, self.prompt = cwd, command, prompt
        self.claude = is_claude(command)
        self.settings = split_settings(command)[1] if self.claude else dict(DEFAULTS)
        self.session = str(uuid.uuid4())
        self.pid, self.restarting = None, False
        self.docked = docked

        css = Gtk.CssProvider()
        css.load_from_data(BAR_CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = window = Gtk.Window(title=title)
        window.set_role(role)
        window.set_icon(self.spark())  # the taskbar's chat icon, also when undocked
        window.set_default_size(900, 640)
        window.connect("delete-event", self.on_delete)
        window.connect("focus-in-event", lambda *_: window.set_urgency_hint(False))
        window.connect("realize", lambda *_: self.publish_dock())

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        bar.set_name("odd1-chat-bar")
        bar.set_size_request(-1, BAR_HEIGHT)
        folder = Gtk.Label(label=self.folder_label(), xalign=0)
        folder.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        folder.set_tooltip_text(cwd)
        folder.set_margin_start(14)
        bar.pack_start(folder, True, True, 0)
        self.gear = self.icon_button(self.draw_gear, "Settings for this chat")
        self.gear.connect("clicked", lambda *_: self.open_settings())
        self.dock_button = self.icon_button(self.draw_dock, "")
        self.dock_button.connect("clicked", lambda *_: self.toggle_dock())
        self.dock_button.set_margin_end(6)
        bar.pack_end(self.dock_button, False, False, 0)
        bar.pack_end(self.gear, False, False, 0)
        self.update_dock_tooltip()

        self.term = term = Vte.Terminal()
        rgba = lambda colour: (lambda c: c if c.parse(colour) else c)(Gdk.RGBA())
        term.set_font(Pango.FontDescription(FONT))
        term.set_colors(rgba(FOREGROUND), rgba(BACKGROUND), [rgba(c) for c in PALETTE])
        term.set_color_cursor(rgba(CURSOR))
        term.set_color_highlight(rgba(SELECTION_BACKGROUND))
        term.set_color_highlight_foreground(rgba(SELECTION))
        term.set_scrollback_lines(10000)
        term.set_cursor_blink_mode(Vte.CursorBlinkMode.OFF)
        term.set_cursor_shape(Vte.CursorShape.IBEAM)
        term.set_audible_bell(False)
        term.connect("bell", lambda *_: None if window.is_active() else window.set_urgency_hint(True))
        term.connect("selection-changed", self.on_selection)
        term.connect("child-exited", self.on_exit)
        term.connect("key-press-event", self.on_key)
        term.connect("button-press-event", self.on_button)
        self.url_tag = term.match_add_regex(Vte.Regex.new_for_match(URL, -1, PCRE2_MULTILINE | PCRE2_CASELESS), 0)
        term.match_set_cursor_name(self.url_tag, "pointer")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(bar, False, False, 0)
        box.pack_start(term, True, True, 0)
        window.add(box)
        window.show_all()
        self.spawn(resume=False)
        term.grab_focus()

    def spark(self, size=64):
        from gi.repository import GdkPixbuf
        from .taskbar import chat_icon
        image = chat_icon(size).convert("RGBA")
        return GdkPixbuf.Pixbuf.new_from_bytes(self.GLib.Bytes.new(image.tobytes()), GdkPixbuf.Colorspace.RGB, True,
                                               8, size, size, size * 4)

    # The bar ---------------------------------------------------------------------------------------------------------
    def folder_label(self):
        home = os.path.expanduser("~")
        return "~" + self.cwd[len(home):] if self.cwd.startswith(home) else self.cwd

    def icon_button(self, painter, tooltip):
        Gtk = self.Gtk
        area = Gtk.DrawingArea()
        area.set_size_request(18, 18)
        area.connect("draw", lambda widget, cr: painter(widget, cr))
        button = Gtk.Button()
        button.add(area)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_tooltip_text(tooltip)
        button.set_can_focus(False)  # the keyboard stays in the terminal
        return button

    def ink(self, widget, cr):
        colour = widget.get_parent().get_style_context().get_color(widget.get_parent().get_state_flags())
        cr.set_source_rgba(colour.red, colour.green, colour.blue, colour.alpha)
        cr.set_line_width(1.5)

    def draw_gear(self, widget, cr):
        """A cog: a ring with eight teeth."""
        import math
        self.ink(widget, cr)
        cx, cy = 9, 9
        for tooth in range(8):
            angle = tooth * math.pi / 4
            cr.move_to(cx + 5.2 * math.cos(angle), cy + 5.2 * math.sin(angle))
            cr.line_to(cx + 7.6 * math.cos(angle), cy + 7.6 * math.sin(angle))
        cr.set_line_width(2.4)
        cr.stroke()
        cr.set_line_width(1.6)
        cr.arc(cx, cy, 5.2, 0, 2 * math.pi)
        cr.stroke()
        cr.arc(cx, cy, 2.0, 0, 2 * math.pi)
        cr.stroke()

    def draw_dock(self, widget, cr):
        """Docked: a window with an arrow leaving it (undock). A window: a frame with the column filled on the right
        (dock it there)."""
        self.ink(widget, cr)
        if self.docked:
            cr.move_to(8, 3.5)
            cr.line_to(3.5, 3.5)
            cr.line_to(3.5, 14.5)
            cr.line_to(14.5, 14.5)
            cr.line_to(14.5, 10)
            cr.stroke()
            cr.move_to(8, 10)
            cr.line_to(15, 3)
            cr.move_to(10, 3)
            cr.line_to(15, 3)
            cr.line_to(15, 8)
            cr.stroke()
        else:
            cr.rectangle(2.5, 3.5, 13, 11)
            cr.stroke()
            cr.rectangle(10.5, 3.5, 5, 11)
            cr.fill()

    def update_dock_tooltip(self):
        self.dock_button.set_tooltip_text("Undock: a window of its own" if self.docked else "Dock in the chat column")

    def toggle_dock(self):
        self.docked = not self.docked
        self.update_dock_tooltip()
        self.dock_button.queue_draw()
        self.publish_dock()

    def publish_dock(self):
        """Ask the shell to dock or undock this window (a property on it, which the shell follows)."""
        gdk_window = self.window.get_window()
        if gdk_window is None:
            return
        try:
            from Xlib import display as xdisplay
            screen = xdisplay.Display()
            window = screen.create_resource_object("window", gdk_window.get_xid())
            window.change_property(screen.intern_atom(DOCK_PROPERTY), screen.intern_atom("CARDINAL"), 32,
                                   [1 if self.docked else 0])
            screen.sync()
            screen.close()
        except Exception as error:  # no X connection for Xlib: docking stays as it is
            print(f"odd1-desktop chat: {error}", file=sys.stderr)

    # Settings --------------------------------------------------------------------------------------------------------
    def open_settings(self):
        Gtk = self.Gtk
        popover = Gtk.Popover.new(self.gear)
        popover.set_position(Gtk.PositionType.BOTTOM)
        grid = Gtk.Grid(row_spacing=8, column_spacing=12)
        grid.set_name("odd1-chat-settings")
        row = 0

        def heading(text):
            nonlocal row
            label = Gtk.Label(label=text, xalign=0)
            label.get_style_context().add_class("heading")
            grid.attach(label, 0, row, 2, 1)
            row += 1

        def line(label, widget):
            nonlocal row
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)
            row += 1

        def choice(choices, current):
            combo = Gtk.ComboBoxText()
            for value, text in choices + (((current, current),) if current not in dict(choices) else ()):
                combo.append(value or "", text)
            combo.set_active_id(current or "")
            return combo

        heading("This chat")
        size = Gtk.Box(spacing=4)
        smaller, bigger = Gtk.Button(label="A−"), Gtk.Button(label="A+")
        percent = Gtk.Label(label=f"{round(self.term.get_font_scale() * 100)}%")
        for button, step in ((smaller, 1 / 1.1), (bigger, 1.1)):
            button.connect("clicked", lambda _, step=step: (
                self.term.set_font_scale(max(0.6, min(2.5, self.term.get_font_scale() * step))),
                percent.set_text(f"{round(self.term.get_font_scale() * 100)}%")))
        size.pack_start(smaller, False, False, 0)
        size.pack_start(percent, True, True, 0)
        size.pack_start(bigger, False, False, 0)
        line("Text size", size)
        if self.claude:
            heading("Claude Code")
            model, effort, mode = (choice(MODELS, self.settings["model"]), choice(EFFORTS, self.settings["effort"]),
                                   choice(MODES, self.settings["mode"]))
            line("Model", model)
            line("Effort", effort)
            line("Permissions", mode)
            verbose = Gtk.Switch(active=self.settings["verbose"], halign=Gtk.Align.START)
            verbose.set_tooltip_text("Show everything Claude does: the code it writes and tool output in full "
                                     "(Claude Code's verbose mode). Off: short summaries.")
            line("Show code being written", verbose)
            note = Gtk.Label(label="Apply restarts Claude Code here;\nthe conversation continues.", xalign=0)
            note.get_style_context().add_class("dim-label")
            grid.attach(note, 0, row, 2, 1)
            row += 1
            apply = Gtk.Button(label="Apply")
            apply.get_style_context().add_class("suggested-action")

            def on_apply(_):
                self.settings = {"model": model.get_active_id() or None, "effort": effort.get_active_id() or None,
                                 "mode": mode.get_active_id() or None, "verbose": verbose.get_active()}
                popover.popdown()
                self.restart()
            apply.connect("clicked", on_apply)
            grid.attach(apply, 1, row, 1, 1)
        grid.show_all()
        popover.add(grid)
        popover.connect("closed", lambda *_: self.term.grab_focus())
        popover.popup()

    # The agent ---------------------------------------------------------------------------------------------------------
    def argv(self, resume):
        if not self.claude:
            return ["bash", "-lc", shell_line(shlex.split(self.command) + ([self.prompt] if self.prompt else []))]
        resume = resume and session_saved(self.session)
        return ["bash", "-lc", shell_line(claude_argv(self.command, self.settings, self.session, resume, self.prompt))]

    def spawn(self, resume):
        Vte, GLib = self.Vte, self.GLib

        def started(_term, pid, error, *_):
            self.pid = pid if pid and pid > 0 else None
            if error:
                print(f"odd1-desktop chat: {error}", file=sys.stderr)
                self.term.feed(f"odd1-desktop chat: {error.message}\r\n".encode())
        # the person's own session: not a child of whichever Claude Code session opened the window (shell.chat_env)
        env = [f"{key}={value}" for key, value in os.environ.items() if not key.startswith("CLAUDE")]
        self.term.spawn_async(Vte.PtyFlags.DEFAULT, self.cwd, self.argv(resume), env, GLib.SpawnFlags.SEARCH_PATH,
                              None, None, -1, None, started)

    def restart(self):
        """Start the agent again with this chat's settings, on the same conversation."""
        if self.pid:
            self.restarting = True
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGHUP)
            except OSError:
                self.restarting = False
        if not self.restarting:
            self.spawn(resume=True)

    def on_exit(self, _term, _status):
        if self.restarting:
            self.restarting = False
            self.term.reset(True, True)  # the resumed conversation shows itself again
            self.term.feed(b"\x1b[2m(Claude Code restarted with this chat's settings)\x1b[0m\r\n")
            self.spawn(resume=True)
            return
        self.Gtk.main_quit()

    def on_delete(self, *_):
        """Close the window, after asking when the agent still runs (its conversation stays resumable)."""
        Gtk = self.Gtk
        if self.pid and busy(self.pid):
            dialog = Gtk.MessageDialog(transient_for=self.window, modal=True, message_type=Gtk.MessageType.QUESTION,
                                       buttons=Gtk.ButtonsType.NONE, text="Close this chat?")
            dialog.format_secondary_text("It is still running. The conversation stays and can be picked up again "
                                         "with claude --resume.")
            dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Close", Gtk.ResponseType.OK)
            answer = dialog.run()
            dialog.destroy()
            if answer != Gtk.ResponseType.OK:
                return True
        if self.pid:
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGHUP)
            except OSError:
                pass
        Gtk.main_quit()
        return False

    # The terminal ------------------------------------------------------------------------------------------------------
    def on_selection(self, term):
        if term.get_has_selection():
            term.copy_clipboard_format(self.Vte.Format.TEXT)

    def on_key(self, term, event):
        Gdk = self.Gdk
        mods = event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
        if mods == Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK:
            key = Gdk.keyval_to_lower(event.keyval)
            if key == Gdk.KEY_c:
                term.copy_clipboard_format(self.Vte.Format.TEXT)
                return True
            if key == Gdk.KEY_v:
                term.paste_clipboard()
                return True
        return False

    def on_button(self, term, event):
        Gdk, Gtk = self.Gdk, self.Gtk
        if event.button == 1 and event.state & Gdk.ModifierType.CONTROL_MASK:
            url, _ = term.match_check_event(event)
            if url:
                subprocess.Popen(["xdg-open", url], start_new_session=True, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return True
        if event.button == 3:
            menu = Gtk.Menu()
            for label, action, sensitive in (("Copy", lambda *_: term.copy_clipboard_format(self.Vte.Format.TEXT),
                                              term.get_has_selection()),
                                             ("Paste", lambda *_: term.paste_clipboard(), True)):
                item = Gtk.MenuItem(label=label)
                item.set_sensitive(sensitive)
                item.connect("activate", action)
                menu.append(item)
            menu.show_all()
            menu.popup_at_pointer(event)
            return True
        return False


def main(args):
    """odd1-desktop chat [--title T] [--role R] [--cwd DIR] [--command CMD] [--prompt TEXT] [--undocked]"""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    GLib.set_prgname("odd1-chat-window")
    ChatWindow(args.title, args.role, args.cwd or os.path.expanduser("~"), args.run or "claude",
               docked=not args.undocked, prompt=args.prompt)
    Gtk.main()
    return 0
