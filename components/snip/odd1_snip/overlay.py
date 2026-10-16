"""The snipping overlay: the frozen screen, dimmed, over everything; drag (or click a window) to snip."""
import cairo
from gi.repository import Gdk, GLib, Gtk

from . import shapes

LABELS = {"rect": "Rectangle", "free": "Freeform", "window": "Window", "full": "Full screen"}
ACCENT = (0.98, 0.45, 0.09)  # Odd1 orange

CSS = b"""
.snip-bar { background: #181A1D; border-radius: 10px; padding: 6px; margin-top: 14px; }
.snip-bar button { color: #E4E4E8; background: none; border: none; box-shadow: none; padding: 6px 12px;
                   border-radius: 6px; font-family: Inter; }
.snip-bar button:hover { background: #2C2F35; }
.snip-bar button:checked { background: #3A3D45; color: #FFFFFF; }
"""


def screen_pixbuf():
    root = Gdk.get_default_root_window()
    return Gdk.pixbuf_get_from_window(root, 0, 0, root.get_width(), root.get_height())


class Overlay(Gtk.Window):
    """Calls done(pixbuf) with the snip, or done(None) when the person cancels (Esc, the x, a right click)."""

    def __init__(self, mode, done, windows=()):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.done, self.mode, self.windows = done, mode, list(windows)
        self.shot = screen_pixbuf()
        self.W, self.H = self.shot.get_width(), self.shot.get_height()
        self.start = self.end = None
        self.path = []
        self.hover = None
        self.finished = False

        self.move(0, 0)
        self.set_default_size(self.W, self.H)
        self.set_app_paintable(True)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        stack = Gtk.Overlay()
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(self.W, self.H)
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.POINTER_MOTION_MASK)
        self.area.connect("draw", self.on_draw)
        self.area.connect("button-press-event", self.on_press)
        self.area.connect("motion-notify-event", self.on_motion)
        self.area.connect("button-release-event", self.on_release)
        stack.add(self.area)
        stack.add_overlay(self.toolbar())
        self.add(stack)
        self.connect("key-press-event", self.on_key)
        self.connect("map-event", self.on_map)

    def toolbar(self):
        bar = Gtk.Box(spacing=2, halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        bar.get_style_context().add_class("snip-bar")
        self.buttons = {}
        group = None
        for mode in shapes.MODES:
            b = Gtk.RadioButton.new_with_label_from_widget(group, LABELS[mode])
            b.set_mode(False)  # a button, not a radio dot
            group = group or b
            b.set_active(mode == self.mode)
            b.connect("toggled", self.on_mode, mode)
            b.connect("clicked", self.on_mode_click, mode)
            self.buttons[mode] = b
            bar.pack_start(b, False, False, 0)
        close = Gtk.Button(label="✕")
        close.set_tooltip_text("Cancel (Esc)")
        close.connect("clicked", lambda *_: self.finish(None))
        bar.pack_start(close, False, False, 0)
        return bar

    def on_map(self, *_):
        win = self.get_window()
        win.set_cursor(Gdk.Cursor.new_from_name(win.get_display(), "crosshair"))
        # An override-redirect window gets no focus from the window manager: take the keyboard (for Esc) and pointer.
        # The person asked for the overlay; it lets go the moment it closes.
        GLib.timeout_add(50, self.grab)

    def grab(self):
        seat = Gdk.Display.get_default().get_default_seat()
        seat.grab(self.get_window(), Gdk.SeatCapabilities.ALL, True, None, None, None)
        return False

    def on_mode(self, button, mode):
        if button.get_active():
            self.mode = mode
            self.start = self.end = None
            self.path = []
            self.area.queue_draw()

    def on_mode_click(self, button, mode):
        if mode == "full" and button.get_active():
            self.finish(self.shot)

    def on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.finish(None)
        elif ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.mode == "full":
            self.finish(self.shot)
        return True

    # --- pointer

    def on_press(self, _w, ev):
        if ev.button == 3:
            self.finish(None)
            return
        if ev.button != 1:
            return
        x, y = int(ev.x), int(ev.y)
        if self.mode == "full":
            self.finish(self.shot)
        elif self.mode == "window":
            r = shapes.window_at(self.windows, x, y) or (0, 0, self.W, self.H)
            self.finish(self.cut(r))
        else:
            self.start = self.end = (x, y)
            self.path = [(x, y)]

    def on_motion(self, _w, ev):
        x, y = int(ev.x), int(ev.y)
        if self.mode == "window":
            r = shapes.window_at(self.windows, x, y)
            if r != self.hover:
                self.hover = r
                self.area.queue_draw()
        elif self.start:
            self.end = (x, y)
            if self.mode == "free":
                self.path.append((x, y))
            self.area.queue_draw()

    def on_release(self, _w, ev):
        if ev.button != 1 or not self.start:
            return
        if self.mode == "rect":
            r = shapes.clip(shapes.rect(*self.start, *self.end), self.W, self.H)
            snip = self.cut(r) if shapes.big_enough(r) else None
        else:
            r = shapes.clip(shapes.bbox(self.path), self.W, self.H) if len(self.path) > 2 else None
            snip = self.cut_free(r) if shapes.big_enough(r) else None
        self.start = self.end = None
        self.path = []
        if snip:
            self.finish(snip)
        else:
            self.area.queue_draw()  # a click: start over

    # --- the snip

    def cut(self, r):
        x, y, w, h = shapes.clip(r, self.W, self.H)
        return self.shot.new_subpixbuf(x, y, w, h).copy()

    def cut_free(self, r):
        """The freeform shape on white, as Windows does, cut to the rectangle around it."""
        x, y, w, h = r
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        cr = cairo.Context(surface)
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.translate(-x, -y)
        cr.move_to(*self.path[0])
        for p in self.path[1:]:
            cr.line_to(*p)
        cr.close_path()
        cr.clip()
        Gdk.cairo_set_source_pixbuf(cr, self.shot, 0, 0)
        cr.paint()
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, w, h)

    def finish(self, pixbuf):
        if self.finished:
            return
        self.finished = True
        Gdk.Display.get_default().get_default_seat().ungrab()
        self.destroy()
        # Let the overlay leave the screen before anything else (a new snip, the editor) is drawn.
        GLib.idle_add(self.hand_over, pixbuf)

    def hand_over(self, pixbuf):
        self.done(pixbuf)
        return False

    # --- drawing

    def on_draw(self, _w, cr):
        Gdk.cairo_set_source_pixbuf(cr, self.shot, 0, 0)
        cr.paint()
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.paint()
        shape = None
        if self.mode == "rect" and self.start:
            shape = lambda: cr.rectangle(*shapes.rect(*self.start, *self.end))
        elif self.mode == "free" and len(self.path) > 1:
            def shape():
                cr.move_to(*self.path[0])
                for p in self.path[1:]:
                    cr.line_to(*p)
                cr.close_path()
        elif self.mode == "window" and self.hover:
            shape = lambda: cr.rectangle(*self.hover)
        if shape:
            cr.save()
            shape()
            cr.clip()
            Gdk.cairo_set_source_pixbuf(cr, self.shot, 0, 0)
            cr.paint()
            cr.restore()
            shape()
            cr.set_source_rgb(*ACCENT)
            cr.set_line_width(2)
            cr.stroke()
        if self.mode == "rect" and self.start:
            x, y, w, h = shapes.rect(*self.start, *self.end)
            label(cr, f"{w} × {h}", x, y + h + 6)
        return False


def label(cr, text, x, y):
    cr.select_font_face("Inter", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(12)
    ext = cr.text_extents(text)
    cr.set_source_rgba(0.09, 0.1, 0.11, 0.9)
    cr.rectangle(x, y, ext.width + 12, 20)
    cr.fill()
    cr.set_source_rgb(0.9, 0.9, 0.91)
    cr.move_to(x + 6, y + 14)
    cr.show_text(text)


def snip(mode, done, windows=()):
    """Show the overlay (after the screen is frozen) and call done(pixbuf or None)."""
    win = Overlay(mode, done, windows)
    win.show_all()
    return win

