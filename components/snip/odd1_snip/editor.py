"""The snip's window: mark it up (pen, highlighter), crop it, undo; every change is copied to the clipboard."""
import cairo
from gi.repository import Gdk, GLib, Gtk

from . import shapes
from .overlay import LABELS

HIGHLIGHT = Gdk.RGBA(1.0, 0.86, 0.0, 0.45)


def to_surface(pixbuf):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, pixbuf.get_width(), pixbuf.get_height())
    cr = cairo.Context(surface)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint()
    return surface


def draw_stroke(cr, stroke):
    tool, rgba, points = stroke
    cr.save()
    if tool == "highlight":
        cr.set_operator(cairo.OPERATOR_MULTIPLY)
        cr.set_line_width(18)
        cr.set_line_cap(cairo.LINE_CAP_SQUARE)
    else:
        cr.set_line_width(3)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha)
    cr.move_to(*points[0])
    for p in points[1:] or points:  # a single click is a dot
        cr.line_to(*p)
    cr.stroke()
    cr.restore()


class Editor(Gtk.Window):
    """The snip's window. `new_snip(mode)` is called for New; `path` is where the snip was saved."""

    def __init__(self, new_snip):
        super().__init__(title="Snip")
        self.new_snip = new_snip
        self.base = None
        self.strokes = []
        self.history = []  # ("stroke",) or ("crop", base, strokes): what Undo takes back
        self.current = None  # the stroke or crop being dragged
        self.path = None
        self.copy_pending = False
        self.set_icon_name("applets-screenshooter")

        bar = Gtk.HeaderBar(show_close_button=True, title="Snip")
        self.set_titlebar(bar)
        self.header = bar

        new = Gtk.MenuButton(label="New")
        new.set_tooltip_text("Take a new snip")
        menu = Gtk.Menu()
        for mode in shapes.MODES:
            item = Gtk.MenuItem(label=LABELS[mode])
            item.connect("activate", lambda _i, m=mode: self.new_snip(m))
            menu.append(item)
        menu.show_all()
        new.set_popup(menu)
        bar.pack_start(new)

        self.tools = {}
        group = None
        for tool, label, tip in (("pen", "Pen", "Draw (P)"), ("highlight", "Highlighter", "Highlight (H)"),
                                 ("crop", "Crop", "Drag the part to keep (C)")):
            b = Gtk.RadioButton.new_with_label_from_widget(group, label)
            b.set_mode(False)
            b.set_tooltip_text(tip)
            group = group or b
            self.tools[tool] = b
            bar.pack_start(b)
        self.color = Gtk.ColorButton(rgba=Gdk.RGBA(0.86, 0.1, 0.1, 1))
        self.color.set_tooltip_text("Pen colour")
        bar.pack_start(self.color)
        self.undo_button = self.button("edit-undo-symbolic", "Undo (Ctrl+Z)", self.undo)
        bar.pack_start(self.undo_button)

        bar.pack_end(self.button("document-save-as-symbolic", "Save as (Ctrl+Shift+S)", self.save_as))
        bar.pack_end(self.button("document-save-symbolic", "Save (Ctrl+S)", self.save))
        bar.pack_end(self.button("edit-copy-symbolic", "Copy (Ctrl+C)", self.copy))

        self.area = Gtk.DrawingArea()
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.BUTTON_MOTION_MASK)
        self.area.connect("draw", self.on_draw)
        self.area.connect("button-press-event", self.on_press)
        self.area.connect("motion-notify-event", self.on_motion)
        self.area.connect("button-release-event", self.on_release)
        self.area.set_halign(Gtk.Align.CENTER)
        self.area.set_valign(Gtk.Align.CENTER)
        scroll = Gtk.ScrolledWindow()
        scroll.add(self.area)
        self.add(scroll)
        self.connect("key-press-event", self.on_key)

    def button(self, icon, tip, action):
        b = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
        b.set_tooltip_text(tip)
        b.connect("clicked", lambda *_: action())
        return b

    def show_snip(self, pixbuf, path):
        self.base = to_surface(pixbuf)
        self.strokes, self.history = [], []
        self.path = path
        self.tools["pen"].set_active(True)
        self.refresh()
        w, h = self.base.get_width(), self.base.get_height()
        screen = Gdk.Screen.get_default()
        self.resize(min(max(w + 40, 860), int(screen.get_width() * 0.8)),
                    min(h + 40, int(screen.get_height() * 0.8)))
        self.show_all()
        self.present()
        self.note("Copied to the clipboard")

    def note(self, text):
        self.header.set_subtitle(f"{text} · {self.path.name}" if self.path else text)

    # --- the image

    def flatten(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.base.get_width(), self.base.get_height())
        cr = cairo.Context(surface)
        cr.set_source_surface(self.base)
        cr.paint()
        for s in self.strokes:
            draw_stroke(cr, s)
        return surface

    def pixbuf(self):
        s = self.flatten()
        return Gdk.pixbuf_get_from_surface(s, 0, 0, s.get_width(), s.get_height())

    def refresh(self):
        self.area.set_size_request(self.base.get_width(), self.base.get_height())
        self.undo_button.set_sensitive(bool(self.history))
        self.area.queue_draw()

    def changed(self):
        """Every change is on the clipboard at once (as Windows' snipping tool does), once the drag is over."""
        self.refresh()
        if not self.copy_pending:
            self.copy_pending = True
            GLib.timeout_add(150, self.copy_later)

    def copy_later(self):
        self.copy_pending = False
        self.copy(quiet=True)
        return False

    # --- actions

    def copy(self, quiet=False):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_image(self.pixbuf())
        if not quiet:
            self.note("Copied to the clipboard")

    def save(self):
        self.pixbuf().savev(str(self.path), "png", [], [])
        self.note("Saved")

    def save_as(self):
        dialog = Gtk.FileChooserDialog(title="Save snip", parent=self, action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.ACCEPT)
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_folder(str(self.path.parent))
        dialog.set_current_name(self.path.name)
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            name = dialog.get_filename()
            kind = "jpeg" if name.lower().endswith((".jpg", ".jpeg")) else "png"
            self.pixbuf().savev(name, kind, [], [])
            self.note(f"Saved as {name}")
        dialog.destroy()

    def undo(self):
        if not self.history:
            return
        step = self.history.pop()
        if step[0] == "stroke":
            self.strokes.pop()
        else:
            _, self.base, self.strokes = step
        self.changed()

    def on_key(self, _w, ev):
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        shift = ev.state & Gdk.ModifierType.SHIFT_MASK
        key = Gdk.keyval_to_lower(ev.keyval)
        if ctrl and key == Gdk.KEY_z:
            self.undo()
        elif ctrl and key == Gdk.KEY_c:
            self.copy()
        elif ctrl and key == Gdk.KEY_s:
            self.save_as() if shift else self.save()
        elif ctrl and key == Gdk.KEY_n:
            self.new_snip("rect")
        elif not ctrl and key in (Gdk.KEY_p, Gdk.KEY_h, Gdk.KEY_c):
            self.tools[{Gdk.KEY_p: "pen", Gdk.KEY_h: "highlight", Gdk.KEY_c: "crop"}[key]].set_active(True)
        elif key == Gdk.KEY_Escape and self.current:
            self.current = None
            self.area.queue_draw()
        else:
            return False
        return True

    # --- pointer

    def tool(self):
        return next(t for t, b in self.tools.items() if b.get_active())

    def on_press(self, _w, ev):
        if ev.button != 1:
            return
        p = (ev.x, ev.y)
        tool = self.tool()
        if tool == "crop":
            self.current = ("crop", p, p)
        else:
            rgba = HIGHLIGHT if tool == "highlight" else self.color.get_rgba()
            self.current = (tool, rgba, [p])
        self.area.queue_draw()

    def on_motion(self, _w, ev):
        if not self.current:
            return
        p = (ev.x, ev.y)
        if self.current[0] == "crop":
            self.current = ("crop", self.current[1], p)
        else:
            self.current[2].append(p)
        self.area.queue_draw()

    def on_release(self, _w, ev):
        if ev.button != 1 or not self.current:
            return
        current, self.current = self.current, None
        if current[0] == "crop":
            r = shapes.clip(tuple(int(v) for v in shapes.rect(*current[1], *current[2])),
                            self.base.get_width(), self.base.get_height())
            if not shapes.big_enough(r):
                self.area.queue_draw()
                return
            self.history.append(("crop", self.base, self.strokes))
            flat = self.flatten()
            x, y, w, h = r
            self.base = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
            cr = cairo.Context(self.base)
            cr.set_source_surface(flat, -x, -y)
            cr.paint()
            self.strokes = []
            self.tools["pen"].set_active(True)
        else:
            self.strokes.append(current)
            self.history.append(("stroke",))
        self.changed()

    def on_draw(self, _w, cr):
        if not self.base:
            return
        cr.set_source_surface(self.base)
        cr.paint()
        for s in self.strokes:
            draw_stroke(cr, s)
        if self.current and self.current[0] == "crop":
            x, y, w, h = shapes.rect(*self.current[1], *self.current[2])
            cr.set_source_rgba(0, 0, 0, 0.45)
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            cr.rectangle(0, 0, self.base.get_width(), self.base.get_height())
            cr.rectangle(x, y, w, h)
            cr.fill()
            cr.set_source_rgb(0.98, 0.45, 0.09)
            cr.set_line_width(2)
            cr.rectangle(x, y, w, h)
            cr.stroke()
        elif self.current:
            draw_stroke(cr, self.current)
