"""The Odd1 desktop shell: the chat column on the right, the wallpaper, the taskbar, and keeping apps out of both.

Every window titled like a chat ("Claude Code") becomes part of the column: retyped as a dock window below the
column's header, where apps cannot cover it. The header shows a tab per chat and a "New chat" button; the
header's window carries the EWMH strut that reserves the column, so maximized windows and every app's work area
stop at it. Docks get no focus from the window manager, so a click in the column focuses the shown chat here.
New app windows that would overlap the column are moved into the work area once. The taskbar (taskbar.py) shows
what is running. The wallpaper goes to xfdesktop (which draws it and the desktop icons) and to the root window.
Everything follows the screen size (Remote Desktop resizes the screen with the viewer's window).

Window-manager independent (EWMH, XInput 2, RandR), so it runs on any X display.
"""
import json
import os
import select
import shutil
import subprocess
import time

from . import layout

NEW_CHAT = "+  New chat"
GENERIC_EVENT = 35  # X11 GenericEvent (XInput 2 events arrive as these)
AGENT_POLL_S = 2  # how often the taskbar recounts the agent desktop's windows
# Launchers put on the desktop once (the person may remove them): Files, the web, a terminal, documents, notes,
# sums; with plain names. LibreOffice keeps its own icon (Colloid draws it as a blank page).
DESKTOP_APPS = {
    "thunar.desktop": {"Name": "Files"},
    "firefox.desktop": {},
    "xfce4-terminal.desktop": {"Name": "Terminal"},
    "startcenter.desktop": {"Name": "LibreOffice", "Icon": "/run/current-system/sw/share/icons/hicolor/256x256/apps/libreoffice-startcenter.png"},
    "org.xfce.mousepad.desktop": {"Name": "Text Editor"},
    "org.gnome.Calculator.desktop": {},
}
APPLICATIONS = "/run/current-system/sw/share/applications"


def log(message):
    if os.environ.get("ODD1_DESKTOP_DEBUG"):
        print(message, flush=True)


def chat_command():
    """The command a new Claude chat runs: Claude Code by default; any agent harness via $ODD1_CHAT_COMMAND."""
    return os.environ.get("ODD1_CHAT_COMMAND") or "claude"


def load_agents():
    """The agent systems a person can talk to, Claude first: {name: {title, description, command}} from
    $ODD1_AGENTS (/etc/odd1/agents.json, nix/odd1.nix's odd1.agents)."""
    try:
        with open(os.environ.get("ODD1_AGENTS", "/etc/odd1/agents.json")) as f:
            agents = json.load(f)
    except (OSError, ValueError):
        agents = {}
    agents.setdefault("claude", {"title": "Claude", "description": "A new conversation with Claude Code"})
    agents["claude"]["command"] = chat_command() if os.environ.get("ODD1_CHAT_COMMAND") else \
        agents["claude"].get("command", "claude")
    return dict(sorted(agents.items(), key=lambda item: item[0] != "claude"))


def open_chat(cwd=None, agent="claude"):
    """A new chat window with an agent system: a terminal carrying the role odd1-agent:<name> (the shell docks it
    and labels its tab), running the system's command. It runs in its own systemd scope, so a chat outlives the
    shell (and the shell's restarts)."""
    agents = load_agents()
    entry = agents.get(agent) or agents["claude"]
    title = layout.CHAT_TITLES[0] if agent == "claude" else entry["title"]
    command = f"{entry['command']}; exec bash"
    scope = ["systemd-run", "--user", "--scope", "--collect", "--quiet", f"--unit=odd1-chat-{time.time_ns()}"]
    subprocess.Popen((scope if shutil.which("systemd-run") else []) + [
        "xfce4-terminal", "--disable-server", f"--title={title}", f"--role={layout.AGENT_ROLE}{agent}",
        f"--working-directory={cwd or os.path.expanduser('~')}", "-x", "bash", "-lc", command],
        start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launcher(text, overrides):
    """A .desktop file's text with some [Desktop Entry] keys replaced (and their translations dropped)."""
    lines, section = [], None
    for line in text.splitlines():
        if line.startswith("["):
            section = line.strip()
        key = line.split("=", 1)[0].split("[", 1)[0].strip()
        if section == "[Desktop Entry]" and key in overrides:
            if "[" not in line.split("=", 1)[0]:
                lines.append(f"{key}={overrides[key]}")
            continue
        lines.append(line)
    return "\n".join(lines) + "\n"


def silence(screen, name, libx11=None):
    """Turn the X bell off on a display. Apps ring it on every refused key (agents type a lot), the agent desktop
    passes its rings on to the person's display, and Xvnc sends each one to the viewer, where Remote Desktop beeps.
    The volume alone is not enough (a ring may ask for 100%), so XKB's AudibleBell control goes off too (libX11
    through ctypes: python-xlib has no XKB)."""
    import ctypes
    import ctypes.util
    screen.change_keyboard_control(bell_percent=0)
    screen.sync()
    path = libx11 or os.environ.get("ODD1_LIBX11") or ctypes.util.find_library("X11")
    if not path:
        log("bell: no libX11, volume only")
        return False
    x11 = ctypes.CDLL(path)
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XkbChangeEnabledControls.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    connection = x11.XOpenDisplay(name.encode())
    if not connection:
        return False
    try:
        XKB_USE_CORE_KBD, XKB_AUDIBLE_BELL = 0x100, 1 << 9
        return bool(x11.XkbChangeEnabledControls(connection, XKB_USE_CORE_KBD, XKB_AUDIBLE_BELL, 0))
    finally:
        x11.XSync(connection, 0)
        x11.XCloseDisplay(connection)


def seed_desktop(home=None, applications=APPLICATIONS, apps=DESKTOP_APPS):
    """Put the default launchers on ~/Desktop, once (a marker remembers it, so removed ones stay removed).
    Launchers there must be executable to be trusted (xfdesktop asks otherwise)."""
    home = home or os.path.expanduser("~")
    marker = os.path.join(home, ".local/share/odd1-desktop/seeded")
    if os.path.exists(marker):
        return []
    desktop = os.path.join(home, "Desktop")
    os.makedirs(desktop, exist_ok=True)
    placed = []
    for name in apps:
        source, target = os.path.join(applications, name), os.path.join(desktop, name)
        if os.path.exists(source) and not os.path.exists(target):
            with open(source) as original, open(target, "w") as copy:
                copy.write(launcher(original.read(), dict(apps).get(name) or {}))
            os.chmod(target, 0o755)
            placed.append(name)
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    open(marker, "w").close()
    return placed


def font(size, weight="SemiBold"):
    """Inter at `size` px (the UI font), falling back to PIL's default."""
    from PIL import ImageFont
    try:
        path = subprocess.run(["fc-match", "-f", "%{file}", "Inter"], capture_output=True, text=True).stdout
        face = ImageFont.truetype(path, size)
        try:
            face.set_variation_by_name(weight)
        except Exception:
            pass
        return face
    except Exception:
        return ImageFont.load_default()


class Shell:
    def __init__(self, display_name):
        from Xlib import X, display
        from Xlib.ext import randr, xinput
        self.X, self.xinput = X, xinput
        self.name = display_name
        self.display = display.Display(display_name)
        self.root = self.display.screen().root
        self.atom = self.display.intern_atom
        self.root.change_attributes(event_mask=X.PropertyChangeMask | X.StructureNotifyMask)
        randr.select_input(self.root, randr.RRScreenChangeNotifyMask)
        self.root.xinput_select_events([(xinput.AllMasterDevices, xinput.RawButtonPressMask)])
        silence(self.display, display_name)
        self.font = font(13)
        self.size = None
        self.top = 0
        self.header = None
        self.chats = []  # chat windows in the order they opened
        self.chat_agents = {}  # chat window id -> its agent system's name
        self.agents = load_agents()
        self.menu = None  # the New chat menu (an override-redirect window), while it is open
        self.menu_hover = None
        self.shown = None  # the chat on top of the column
        self.fitted = set()  # app windows already kept out of the column once
        self.tabs = []  # (x, width) of each tab in the header
        self.new_chat = (0, 0)  # (x, width) of the New chat button
        self.attention = set()  # chat window ids that want the person (a blinking ball on their tab)
        self.requested = 0.0  # when the person last clicked New chat (that chat may take the focus)
        self.blink, self.blinked = True, 0.0
        self.starting = True  # chats found at start are re-adopted quietly
        self.wallpaper = None  # the current wallpaper image (the taskbar paints over it)
        self.watched = set()  # client windows whose property changes we follow (state, title, icon)
        from .taskbar import Taskbar
        self.taskbar = Taskbar(self)

    # The column -----------------------------------------------------------------------------------------------
    def dock(self, window, box, strut=None):
        """Make a window a dock at box (the window manager places docks where they ask and never covers them)."""
        X, atom = self.X, self.atom
        window.change_property(atom("_NET_WM_WINDOW_TYPE"), atom("ATOM"), 32, [atom("_NET_WM_WINDOW_TYPE_DOCK")])
        if strut:
            window.change_property(atom("_NET_WM_STRUT_PARTIAL"), atom("CARDINAL"), 32, strut)
            window.change_property(atom("_NET_WM_STRUT"), atom("CARDINAL"), 32, strut[:4])
        x, y, width, height = box
        window.configure(x=x, y=y, width=width, height=height)

    def ensure_header(self):
        X = self.X
        if self.header is None:
            self.header = self.root.create_window(
                0, 0, 1, 1, 0, X.CopyFromParent, X.InputOutput, X.CopyFromParent,
                background_pixel=0x17181C, event_mask=X.ExposureMask | X.ButtonPressMask)
            self.header.set_wm_name("Odd1 chat")
            self.header.set_wm_class("odd1-desktop", "Odd1-desktop")
            self.gc = self.header.create_gc()
        box = layout.header_box(self.size)
        self.dock(self.header, box, layout.strut(self.size))
        self.header.map()

    def adopt(self, window, agent="claude"):
        """Take a new chat window into the column (unmap, retype as a dock, map again). It takes the column and the
        keyboard only if the person just asked for it; otherwise it waits as a tab with the attention ball."""
        self.chat_agents[window.id] = agent
        window.unmap()
        self.display.sync()
        time.sleep(0.2)
        self.dock(window, layout.chat_box(self.size))
        window.map()
        self.display.sync()
        time.sleep(0.2)  # focus needs the window viewable
        window.change_attributes(event_mask=self.X.PropertyChangeMask)  # its urgency hint
        self.chats.append(window)
        if layout.person_asked(self.requested, time.monotonic()):
            self.requested = 0.0
            self.show(window)
        elif self.shown is None:
            self.show(window, focus=False)
        else:
            if not self.starting:
                self.attention.add(window.id)
            self.draw_header()

    def show(self, window, focus=True):
        """Put one chat on top of the column and (when the person chose it) give it the keyboard."""
        log(f"show {window.id:#x} focus={focus}")
        self.shown = window
        window.configure(stack_mode=self.X.Above)
        if focus:
            self.attention.discard(window.id)
            self.focus()
        self.draw_header()

    def ring(self, window):
        """A chat's urgency changed: it wants the person, unless they are already in it."""
        try:
            hints = window.get_wm_hints()
            state = window.get_full_property(self.atom("_NET_WM_STATE"), self.X.AnyPropertyType)
        except Exception:
            return
        wants = layout.urgent(hints.flags if hints else 0, list(state.value) if state else [],
                              self.atom("_NET_WM_STATE_DEMANDS_ATTENTION"))
        here = window is self.shown and self.display.get_input_focus().focus == window
        if wants and not here and window.id not in self.attention:
            self.attention.add(window.id)
            self.draw_header()
            self.taskbar.draw()

    def focus(self):
        if self.shown is not None:
            try:
                self.shown.set_input_focus(self.X.RevertToParent, self.X.CurrentTime)
            except Exception:
                pass

    def draw_header(self):
        from PIL import Image, ImageDraw
        if self.header is None:
            return
        _, _, width, height = layout.header_box(self.size)
        image = Image.new("RGB", (width, height), layout.CHAT_BACKGROUND)
        draw = ImageDraw.Draw(image)
        measure = lambda text: round(draw.textlength(text, font=self.font))
        labels = layout.tab_labels(self.chat_titles())
        waiting = [chat.id in self.attention for chat in self.chats]
        self.tabs = layout.tab_boxes(labels, measure, extra=[layout.ATTENTION_ROOM if w else 0 for w in waiting])
        for index, ((x, w), label) in enumerate(zip(self.tabs, labels)):
            active = self.chats[index] is self.shown
            draw.text((x + 12, height // 2), label, font=self.font, anchor="lm",
                      fill=layout.HEADER_TEXT if active else layout.HEADER_TEXT_DIM)
            if waiting[index]:
                cx, cy, r = x + 12 + measure(label) + 10, height // 2, layout.ATTENTION_RADIUS
                draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                             fill=layout.ATTENTION_COLOUR if self.blink else layout.ATTENTION_DIM)
            if active:
                draw.rectangle([x + 10, height - 3, x + w - 10, height - 2], fill=layout.HEADER_ACCENT)
        if not labels:
            draw.text((26, height // 2), "No chat open", font=self.font, anchor="lm", fill=layout.HEADER_TEXT_DIM)
        button = measure(NEW_CHAT) + 24
        self.new_chat = (width - button - 12, button)
        draw.rounded_rectangle([width - button - 12, 6, width - 12, height - 6], radius=7, fill=(40, 42, 50))
        draw.text((width - button / 2 - 12, height // 2), NEW_CHAT, font=self.font, anchor="mm",
                  fill=layout.HEADER_TEXT)
        draw.line([(0, height - 1), (width, height - 1)], fill=layout.CHAT_EDGE)
        data = image.tobytes("raw", "BGRX")
        self.header.put_image(self.gc, 0, 0, width, height, self.X.ZPixmap, 24, 0, data)

    def chat_titles(self):
        """Each chat's agent system's title, in tab order."""
        return [self.agents.get(self.chat_agents.get(chat.id, "claude"), {}).get("title", "Claude")
                for chat in self.chats]

    def click_header(self, x):
        log(f"header click at {x}; tabs {self.tabs}; new chat {self.new_chat}")
        start, width = self.new_chat
        if start <= x < start + width:
            self.toggle_menu()
            return
        self.close_menu()
        index = layout.hit(self.tabs, x)
        if index is not None and index < len(self.chats):
            self.show(self.chats[index])

    # The New chat menu: one item per agent system ----------------------------------------------------------------
    def start_chat(self, agent):
        self.requested = time.monotonic()  # the person asked: this chat may take the column and the keyboard
        open_chat("/srv/odd1" if agent == "claude" and os.path.isdir("/srv/odd1") else None, agent)

    def toggle_menu(self):
        if self.menu is not None:
            self.close_menu()
            return
        self.agents = load_agents()
        if len(self.agents) == 1:
            self.start_chat("claude")
            return
        X = self.X
        x, y, width, height = layout.menu_box(layout.header_box(self.size), self.new_chat, len(self.agents))
        self.menu = self.root.create_window(
            x, y, width, height, 0, X.CopyFromParent, X.InputOutput, X.CopyFromParent, override_redirect=True,
            background_pixel=0x202229,
            event_mask=X.ExposureMask | X.ButtonPressMask | X.PointerMotionMask | X.LeaveWindowMask)
        self.menu_gc, self.menu_hover = self.menu.create_gc(), None
        self.menu.map()
        self.menu.configure(stack_mode=X.Above)
        self.draw_menu()

    def close_menu(self):
        if self.menu is not None:
            self.menu.destroy()
            self.menu = None

    def draw_menu(self):
        from PIL import Image, ImageDraw
        if self.menu is None:
            return
        names = list(self.agents)
        _, _, width, height = layout.menu_box(layout.header_box(self.size), self.new_chat, len(names))
        image = Image.new("RGB", (width, height), layout.MENU_BACKGROUND)
        draw = ImageDraw.Draw(image)
        small = font(12, "Regular")
        for index, name in enumerate(names):
            top = layout.MENU_PAD + index * layout.MENU_ITEM
            if index == self.menu_hover:
                draw.rounded_rectangle([4, top + 2, width - 5, top + layout.MENU_ITEM - 3], radius=6,
                                       fill=layout.MENU_HOVER)
            entry = self.agents[name]
            draw.text((16, top + 17), entry.get("title", name), font=self.font, anchor="lm", fill=layout.HEADER_TEXT)
            draw.text((16, top + 36), entry.get("description", ""), font=small, anchor="lm",
                      fill=layout.HEADER_TEXT_DIM)
        draw.rectangle([0, 0, width - 1, height - 1], outline=layout.CHAT_EDGE)
        self.menu.put_image(self.menu_gc, 0, 0, width, height, self.X.ZPixmap, 24, 0, image.tobytes("raw", "BGRX"))

    def hover_menu(self, y):
        index = None if y is None else layout.menu_hit(y, len(self.agents))
        if index != self.menu_hover:
            self.menu_hover = index
            self.draw_menu()

    def click_menu(self, y):
        index = layout.menu_hit(y, len(self.agents))
        self.close_menu()
        if index is not None:
            self.start_chat(list(self.agents)[index])

    def pointer_in_menu_or_button(self):
        """Whether the pointer is on the open menu or on the New chat button (whose own click toggles the menu)."""
        pointer = self.root.query_pointer()
        x, y, width, height = layout.menu_box(layout.header_box(self.size), self.new_chat, len(self.agents))
        if x <= pointer.root_x < x + width and y <= pointer.root_y < y + height:
            return True
        header_x, header_y, _, header_height = layout.header_box(self.size)
        start, button = self.new_chat
        return (header_x + start <= pointer.root_x < header_x + start + button
                and header_y <= pointer.root_y < header_y + header_height)

    # The rest of the screen -----------------------------------------------------------------------------------
    def paint_wallpaper(self):
        """Draw the wallpaper for this size, set it on the root window and hand it to xfdesktop for every monitor
        (a new file name every time: xfdesktop reloads only when the name changes)."""
        width, height = self.size
        folder = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "odd1-desktop")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"wallpaper-{self.name.lstrip(':')}-{width}x{height}-{time.time_ns()}.png")
        for old in os.listdir(folder):
            if old.startswith(f"wallpaper-{self.name.lstrip(':')}-") and os.path.join(folder, old) != path:
                os.remove(os.path.join(folder, old))
        self.wallpaper = layout.wallpaper(self.size, layout.dock_width(width), self.mark(layout.watermark_height(height)))
        self.wallpaper.save(path)
        env = {**os.environ, "DISPLAY": self.name}
        subprocess.run(["hsetroot", "-full", path], check=False, env=env)
        for monitor in self.monitors():
            base = f"/backdrop/screen0/monitor{monitor}/workspace0"
            for prop, kind, value in (("image-style", "int", "1"), ("last-image", "string", path)):
                subprocess.run(["xfconf-query", "-c", "xfce4-desktop", "-p", f"{base}/{prop}", "-n", "-t", kind,
                                "-s", value], check=False, env=env, stderr=subprocess.DEVNULL)

    def size_panel(self):
        """Make the top bar span the work area only (the chat column has its tabs at the top), so the clock sits at
        the work area's corner."""
        length = f"{layout.panel_length(self.size[0]):.4f}"
        subprocess.run(["xfconf-query", "-c", "xfce4-panel", "-p", "/panels/panel-1/length", "-n", "-t", "double",
                        "-s", length], check=False, env={**os.environ, "DISPLAY": self.name},
                       stderr=subprocess.DEVNULL)

    def mark(self, height):
        """The owl ($ODD1_MARK, an SVG) rendered `height` pixels tall, or None if it cannot be."""
        from PIL import Image
        source = os.environ.get("ODD1_MARK")
        if not source or not shutil.which("rsvg-convert"):
            return None
        out = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "odd1-desktop", f"mark-{height}.png")
        if not os.path.exists(out):
            subprocess.run(["rsvg-convert", "-h", str(height), source, "-o", out], check=False)
        try:
            return Image.open(out).convert("RGBA")
        except OSError:
            return None

    def monitors(self):
        """Names of the connected outputs (xfdesktop keeps a backdrop per output name)."""
        from Xlib.ext import randr
        try:
            resources = randr.get_screen_resources(self.root)
            for output in resources.outputs:
                info = randr.get_output_info(self.display, output, resources.config_timestamp)
                if info.connection == 0:
                    yield info.name
        except Exception:
            return

    def clients(self):
        listed = self.root.get_full_property(self.atom("_NET_CLIENT_LIST"), self.X.AnyPropertyType)
        for xid in (listed.value if listed else []):
            window = self.display.create_resource_object("window", xid)
            try:
                name = window.get_full_property(self.atom("_NET_WM_NAME"), self.atom("UTF8_STRING"))
                title = name.value.decode("utf-8", "replace") if name else (window.get_wm_name() or "")
                kind = window.get_full_property(self.atom("_NET_WM_WINDOW_TYPE"), self.X.AnyPropertyType)
            except Exception:  # it went away
                continue
            yield window, title, list(kind.value) if kind else []

    def frame_extents(self, window):
        extents = window.get_full_property(self.atom("_NET_FRAME_EXTENTS"), self.X.AnyPropertyType)
        return tuple(extents.value) if extents and len(extents.value) == 4 else (0, 0, 0, 0)

    def keep_out_of_column(self, window):
        """Move a new app window that overlaps the column into the work area (once; afterwards it is the
        person's)."""
        try:
            geometry = window.get_geometry()
            origin = window.translate_coords(self.root, 0, 0)
        except Exception:
            return
        left, right, top, bottom = self.frame_extents(window)
        outer = (-origin.x - left, -origin.y - top, geometry.width + left + right, geometry.height + top + bottom)
        fitted = layout.fit(outer, layout.work_area(self.size, self.top, layout.taskbar_reserved()))
        if fitted:
            x, y, width, height = fitted
            from Xlib.protocol import event
            data = [(1 << 8) | (1 << 9) | (1 << 10) | (1 << 11) | 1, x, y, width - left - right,
                    height - top - bottom]
            message = event.ClientMessage(window=window, client_type=self.atom("_NET_MOVERESIZE_WINDOW"),
                                          data=(32, data))
            self.root.send_event(message, event_mask=self.X.SubstructureRedirectMask | self.X.SubstructureNotifyMask)

    def bar_height(self):
        """The top bar's height: the largest top strut among the other docks (the panel)."""
        height = 0
        for window, _, kind in self.clients():
            if (self.atom("_NET_WM_WINDOW_TYPE_DOCK") in kind and window not in self.chats
                    and window not in (self.header, self.taskbar.window)):
                partial = window.get_full_property(self.atom("_NET_WM_STRUT_PARTIAL"), self.X.AnyPropertyType)
                if partial and len(partial.value) >= 3:
                    height = max(height, int(partial.value[2]))
        return height

    def layout(self):
        size = self.root.get_geometry()
        size = (size.width, size.height)
        top = self.bar_height()
        if (size, top) != (self.size, self.top):
            self.size, self.top = size, top
            self.ensure_header()
            self.size_panel()
            for chat in self.chats:
                self.dock(chat, layout.chat_box(size))
            self.paint_wallpaper()
            self.draw_header()
        alive, apps, agents = set(), [], None
        normal = self.atom("_NET_WM_WINDOW_TYPE_NORMAL")
        for window, title, kind in self.clients():
            alive.add(window.id)
            if window in self.chats or window in (self.header, self.taskbar.window):
                continue
            agent = layout.agent_name(self.role(window))
            if agent or layout.is_chat(title):
                self.adopt(window, agent or "claude")
                continue
            if kind and normal not in kind:
                continue
            if window.id not in self.watched:
                self.watched.add(window.id)
                window.change_attributes(event_mask=self.X.PropertyChangeMask)
            if title == layout.AGENT_DESKTOP_TITLE:
                agents = window
                continue
            if self.atom("_NET_WM_STATE_SKIP_TASKBAR") not in self.taskbar.state(window):
                apps.append((window, self.app_class(window), title))
            if window.id not in self.fitted:
                self.fitted.add(window.id)
                self.keep_out_of_column(window)
        for xid in self.watched - alive:
            self.taskbar.forget_icon(xid)
        self.watched &= alive
        gone = [chat for chat in self.chats if chat.id not in alive]
        if gone:
            self.chats = [chat for chat in self.chats if chat.id in alive]
            if self.shown in gone:
                self.shown = None
                if self.chats:
                    self.show(self.chats[-1], focus=False)
            self.draw_header()
        self.taskbar.update(apps, agents)
        self.display.flush()

    # Windows ------------------------------------------------------------------------------------------------------
    def role(self, window):
        """WM_WINDOW_ROLE, or "" (a chat's terminal names its agent system there)."""
        try:
            prop = window.get_full_property(self.atom("WM_WINDOW_ROLE"), self.X.AnyPropertyType)
            return prop.value.decode("utf-8", "replace") if prop else ""
        except Exception:
            return ""

    def app_class(self, window):
        try:
            names = window.get_wm_class()
            return names[1] if names else ""
        except Exception:
            return ""

    def client_message(self, window, kind, data):
        from Xlib.protocol import event
        message = event.ClientMessage(window=window, client_type=self.atom(kind), data=(32, data + [0] * (5 - len(data))))
        self.root.send_event(message, event_mask=self.X.SubstructureRedirectMask | self.X.SubstructureNotifyMask)

    def activate(self, window):
        self.client_message(window, "_NET_ACTIVE_WINDOW", [2, self.X.CurrentTime, 0])  # 2: from a pager

    def minimize(self, window):
        self.client_message(window, "WM_CHANGE_STATE", [3])  # IconicState

    def topmost(self, windows):
        """The highest of these windows in the stacking order."""
        stacking = self.root.get_full_property(self.atom("_NET_CLIENT_LIST_STACKING"), self.X.AnyPropertyType)
        order = {xid: index for index, xid in enumerate(stacking.value if stacking else [])}
        return max(windows, key=lambda window: order.get(window.id, -1))

    def pointer_in_chat(self):
        pointer = self.root.query_pointer()
        x, y, width, height = layout.chat_box(self.size)
        return x <= pointer.root_x < x + width and y <= pointer.root_y < y + height

    def serve(self):
        X = self.X
        try:
            seed_desktop()
        except OSError as error:
            log(f"desktop launchers: {error}")
        self.layout()
        self.starting = False
        watched = {self.atom("_NET_CLIENT_LIST"), self.atom("_NET_WORKAREA")}
        ringing = {self.atom("WM_HINTS"), self.atom("_NET_WM_STATE")}
        redraw = {self.atom(name) for name in ("_NET_ACTIVE_WINDOW", "_NET_WM_STATE", "_NET_WM_NAME", "WM_NAME",
                                               "_NET_WM_ICON")}
        taskbar = self.taskbar.window
        polled = time.monotonic()
        while True:
            if not self.display.pending_events():
                self.display.flush()
                select.select([self.display], [], [], layout.ATTENTION_BLINK_S if self.attention else AGENT_POLL_S)
            if self.attention and time.monotonic() - self.blinked >= layout.ATTENTION_BLINK_S:
                self.blinked, self.blink = time.monotonic(), not self.blink
                self.draw_header()
                self.taskbar.draw()
            elif not self.attention:
                self.blink = True
            if time.monotonic() - polled >= AGENT_POLL_S:
                polled = time.monotonic()
                count = self.taskbar.count_agent_windows()
                if count != self.taskbar.agent_count:
                    self.layout()  # the Agents item's label and badge change
            if not self.display.pending_events():
                continue
            event = self.display.next_event()
            if event.type == X.Expose and self.header is not None and event.window == self.header:
                self.draw_header()
            elif event.type == X.Expose and event.window == taskbar:
                self.taskbar.draw()
            elif event.type == X.ButtonPress and event.window == taskbar:
                self.taskbar.click(event.event_x)
            elif event.type == X.MotionNotify and event.window == taskbar:
                self.taskbar.motion(event.event_x)
            elif event.type == X.LeaveNotify and event.window == taskbar:
                self.taskbar.leave()
            elif event.type == X.PropertyNotify and event.atom in ringing and event.window in self.chats:
                self.ring(event.window)
            elif event.type == X.PropertyNotify and event.atom in redraw:
                if event.atom == self.atom("_NET_WM_ICON"):
                    self.taskbar.forget_icon(event.window.id)
                self.layout()  # titles, minimized state or focus changed: the taskbar follows
            elif event.type == X.ButtonPress and self.header is not None and event.window == self.header:
                self.click_header(event.event_x)
            elif self.menu is not None and event.type == X.Expose and event.window == self.menu:
                self.draw_menu()
            elif self.menu is not None and event.type == X.ButtonPress and event.window == self.menu:
                self.click_menu(event.event_y)
            elif self.menu is not None and event.type == X.MotionNotify and event.window == self.menu:
                self.hover_menu(event.event_y)
            elif self.menu is not None and event.type == X.LeaveNotify and event.window == self.menu:
                self.hover_menu(None)
            elif event.type == GENERIC_EVENT and getattr(event, "evtype", None) == self.xinput.RawButtonPress:
                if self.menu is not None and not self.pointer_in_menu_or_button():
                    self.close_menu()  # a click anywhere else closes the menu
                if self.pointer_in_chat():
                    self.focus()
                    if self.shown is not None and self.shown.id in self.attention:
                        self.attention.discard(self.shown.id)  # the person clicked into it
                        self.draw_header()
            elif event.type == X.PropertyNotify and event.atom not in watched:
                continue
            elif event.type == X.ConfigureNotify and event.window != self.root:
                continue
            else:
                time.sleep(0.05)  # let a burst (a new window, a resize) settle, then lay out once
                while self.display.pending_events():
                    pending = self.display.next_event()
                    if pending.type == X.Expose and pending.window == self.header:
                        self.draw_header()
                self.layout()
            self.display.flush()


def focus_chat(display_name):
    """Give the keyboard to the chat on top of the column (for a keyboard shortcut)."""
    from Xlib import X, display
    screen = display.Display(display_name)
    root = screen.screen().root
    atom = screen.intern_atom
    stacking = root.get_full_property(atom("_NET_CLIENT_LIST_STACKING"), X.AnyPropertyType)
    for xid in reversed(stacking.value if stacking else []):
        window = screen.create_resource_object("window", xid)
        name = window.get_full_property(atom("_NET_WM_NAME"), atom("UTF8_STRING"))
        if name and layout.is_chat(name.value.decode("utf-8", "replace")):
            window.set_input_focus(X.RevertToParent, X.CurrentTime)
            screen.sync()
            return True
    return False

