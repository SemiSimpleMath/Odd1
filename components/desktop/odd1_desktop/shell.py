"""The Odd1 desktop shell: the chat column on the right, the wallpaper, and keeping apps out of the column.

Every window titled like a chat ("Claude Code") becomes part of the column: retyped as a dock window below the
column's header, where apps cannot cover it. The header shows a tab per chat and a "New chat" button; the
header's window carries the EWMH strut that reserves the column, so maximized windows and every app's work area
stop at it. Docks get no focus from the window manager, so a click in the column focuses the shown chat here.
New app windows that would overlap the column are moved into the work area once. Everything follows the screen
size (Remote Desktop resizes the screen with the viewer's window).

Window-manager independent (EWMH, XInput 2, RandR), so it runs on any X display.
"""
import os
import subprocess
import time

from . import layout

NEW_CHAT = "+  New chat"
GENERIC_EVENT = 35  # X11 GenericEvent (XInput 2 events arrive as these)


def log(message):
    if os.environ.get("ODD1_DESKTOP_DEBUG"):
        print(message, flush=True)


def chat_command():
    """The command a new chat runs: Claude Code by default; any agent harness via $ODD1_CHAT_COMMAND."""
    return os.environ.get("ODD1_CHAT_COMMAND") or "claude"


def open_chat(cwd=None):
    """A new chat window: a terminal titled like a chat (the shell docks it), running the chat command."""
    command = f"{chat_command()}; exec bash"
    subprocess.Popen(["xfce4-terminal", "--disable-server", f"--title={layout.CHAT_TITLES[0]}",
                      f"--working-directory={cwd or os.path.expanduser('~')}", "-x", "bash", "-lc", command],
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
        self.font = font(13)
        self.size = None
        self.top = 0
        self.header = None
        self.chats = []  # chat windows in the order they opened
        self.shown = None  # the chat on top of the column
        self.fitted = set()  # app windows already kept out of the column once
        self.tabs = []  # (x, width) of each tab in the header
        self.new_chat = (0, 0)  # (x, width) of the New chat button

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
        box = layout.header_box(self.size, self.top)
        self.dock(self.header, box, layout.strut(self.size, self.top))
        self.header.map()

    def adopt(self, window):
        """Take a new chat window into the column (unmap, retype as a dock, map again)."""
        window.unmap()
        self.display.sync()
        time.sleep(0.2)
        self.dock(window, layout.chat_box(self.size, self.top))
        window.map()
        self.display.sync()
        time.sleep(0.2)  # focus needs the window viewable
        self.chats.append(window)
        self.show(window)

    def show(self, window):
        """Put one chat on top of the column and give it the keyboard."""
        log(f"show {window.id:#x}")
        self.shown = window
        window.configure(stack_mode=self.X.Above)
        self.focus()
        self.draw_header()

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
        _, _, width, height = layout.header_box(self.size, self.top)
        image = Image.new("RGB", (width, height), layout.CHAT_BACKGROUND)
        draw = ImageDraw.Draw(image)
        measure = lambda text: round(draw.textlength(text, font=self.font))
        labels = layout.tab_labels(len(self.chats)) if self.chats else []
        self.tabs = layout.tab_boxes(labels, measure)
        for index, ((x, w), label) in enumerate(zip(self.tabs, labels)):
            active = self.chats[index] is self.shown
            draw.text((x + 12, height // 2), label, font=self.font, anchor="lm",
                      fill=layout.HEADER_TEXT if active else layout.HEADER_TEXT_DIM)
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

    def click_header(self, x):
        log(f"header click at {x}; tabs {self.tabs}; new chat {self.new_chat}")
        start, width = self.new_chat
        if start <= x < start + width:
            open_chat("/srv/odd1" if os.path.isdir("/srv/odd1") else None)
            return
        index = layout.hit(self.tabs, x)
        if index is not None and index < len(self.chats):
            self.show(self.chats[index])

    # The rest of the screen -----------------------------------------------------------------------------------
    def paint_wallpaper(self):
        path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), f"odd1-wallpaper-{self.name.lstrip(':')}.png")
        layout.wallpaper(self.size, layout.dock_width(self.size[0])).save(path)
        subprocess.run(["hsetroot", "-full", path], check=False, env={**os.environ, "DISPLAY": self.name})

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
        fitted = layout.fit(outer, layout.work_area(self.size, self.top))
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
            if self.atom("_NET_WM_WINDOW_TYPE_DOCK") in kind and window not in self.chats and window != self.header:
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
            for chat in self.chats:
                self.dock(chat, layout.chat_box(size, top))
            self.paint_wallpaper()
            self.draw_header()
        alive = set()
        normal = self.atom("_NET_WM_WINDOW_TYPE_NORMAL")
        for window, title, kind in self.clients():
            alive.add(window.id)
            if window in self.chats or window == self.header:
                continue
            if layout.is_chat(title):
                self.adopt(window)
            elif window.id not in self.fitted and (not kind or normal in kind):
                self.fitted.add(window.id)
                self.keep_out_of_column(window)
        gone = [chat for chat in self.chats if chat.id not in alive]
        if gone:
            self.chats = [chat for chat in self.chats if chat.id in alive]
            if self.shown in gone:
                self.shown = None
                if self.chats:
                    self.show(self.chats[-1])
            self.draw_header()
        self.display.flush()

    def pointer_in_chat(self):
        pointer = self.root.query_pointer()
        x, y, width, height = layout.chat_box(self.size, self.top)
        return x <= pointer.root_x < x + width and y <= pointer.root_y < y + height

    def serve(self):
        X = self.X
        self.layout()
        watched = {self.atom("_NET_CLIENT_LIST"), self.atom("_NET_WORKAREA")}
        while True:
            event = self.display.next_event()
            if event.type == X.Expose and self.header is not None and event.window == self.header:
                self.draw_header()
            elif event.type == X.ButtonPress and self.header is not None and event.window == self.header:
                self.click_header(event.event_x)
            elif event.type == GENERIC_EVENT and getattr(event, "evtype", None) == self.xinput.RawButtonPress:
                if self.pointer_in_chat():
                    self.focus()
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

