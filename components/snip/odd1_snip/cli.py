"""odd1-snip [--mode rect|free|window|full] [--delay SECONDS] [--no-edit]

Freeze the screen and snip a part of it, as Windows' snipping tool does (Print, Super+Shift+S): the snip is copied
to the clipboard, saved to ~/Pictures/Screenshots, and opened for marking up unless --no-edit.
"""
import argparse
import os
import sys

os.environ.setdefault("GDK_SCALE", "1")  # the overlay maps widget pixels to screen pixels one to one
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import overlay, shapes  # noqa: E402


class Snipper:
    def __init__(self, edit=True):
        self.edit = edit
        self.editor = None
        self.owner = None  # our clipboard window once a snip is on the clipboard
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

    def start(self, mode, delay=0):
        if self.editor:
            self.editor.hide()
        # The editor has to be off the screen before the screen is frozen; a delay is for opening a menu first.
        GLib.timeout_add(max(int(delay * 1000), 200), self.freeze, mode)

    def freeze(self, mode):
        try:
            from . import windows
            stack = windows.stacking()
        except Exception as e:  # no window list: Window mode snips the whole screen
            print(f"odd1-snip: no window list ({e})", file=sys.stderr)
            stack = []
        overlay.snip(mode, self.done, stack)
        return False

    def done(self, pixbuf):
        if pixbuf is None:
            if self.editor:
                self.editor.show()
            else:
                self.quit()
            return
        directory = shapes.folder()
        directory.mkdir(parents=True, exist_ok=True)
        path = shapes.free_path(directory, shapes.filename())
        pixbuf.savev(str(path), "png", [], [])
        self.clipboard.set_image(pixbuf)
        self.owner = owner_xid()
        print(path, flush=True)
        if not self.edit:
            self.quit()
            return
        if not self.editor:
            from .editor import Editor
            self.editor = Editor(self.start)
            self.editor.connect("delete-event", self.on_close)
        self.editor.show_snip(pixbuf, path)

    def on_close(self, *_):
        self.editor.hide()
        self.quit()
        return True

    def quit(self):
        """Hold the clipboard until something else takes it: X keeps no copy once its owner is gone."""
        if self.owner is None or owner_xid() != self.owner:
            Gtk.main_quit()
            return
        self.clipboard.connect("owner-change", self.on_owner_change)

    def on_owner_change(self, *_):
        if owner_xid() != self.owner:
            Gtk.main_quit()


def owner_xid():
    """The X window that owns the clipboard (GDK wraps other programs' windows too), or None."""
    owner = Gdk.selection_owner_get(Gdk.SELECTION_CLIPBOARD)
    return owner.get_xid() if owner else None


def main(argv=None):
    p = argparse.ArgumentParser(prog="odd1-snip", description=__doc__.split("\n\n")[1],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=shapes.MODES, default="rect",
                   help="rect (default), free (freeform), window, full (the whole screen)")
    p.add_argument("--delay", type=float, default=0, help="seconds to wait before freezing the screen")
    p.add_argument("--no-edit", action="store_true", help="copy and save only; open no window")
    args = p.parse_args(argv)
    if not Gtk.init_check()[0]:
        sys.exit("odd1-snip: no display (set DISPLAY)")
    Snipper(edit=not args.no_edit).start(args.mode, args.delay)
    Gtk.main()


if __name__ == "__main__":
    main()
