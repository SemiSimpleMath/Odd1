"""Screenshot one window, even when other windows cover it or it hangs off screen.

Odd1's X server has no compositor, so covered pixels do not exist on screen and `import -window` returns black for
them. The X Composite extension renders a redirected window into its own offscreen pixmap instead: redirect the
window's top-level frame (automatic mode, so the screen looks the same), let the app repaint, read the pixmap, crop
the client area. The redirect ends when this connection closes. A minimized (unmapped) window has no pixels at all.

    any-app screenshot WINDOW [-o OUT.png] [--frame] [--display user|agent|:N]   WINDOW: title or app substring (exactly one match), or X11 id
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

SETTLE_SECONDS = 0.3


def match_window(clients, query):
    """Pick one (xid, title, app) from the window manager's client list: an X11 id (0x.. or decimal) or a
    case-insensitive substring of the title or app (WM_CLASS); anything but exactly one match is an error that
    lists the candidates."""
    try:
        xid = int(query, 0)
    except ValueError:
        xid = None
    if xid is not None:
        found = [c for c in clients if c[0] == xid]
        return found[0] if found else (xid, "", "")
    found = [c for c in clients if query.casefold() in f"{c[1]}\n{c[2]}".casefold()]
    if len(found) != 1:
        listing = "; ".join(f"{hex(i)} {t} ({app})" for i, t, app in clients)
        raise LookupError(f"{len(found)} windows match {query!r}; windows: {listing}")
    return found[0]


def client_box(offset, size, frame_size, frame):
    """The crop box (left, top, right, bottom) inside the frame pixmap: the whole frame or just the client."""
    if frame:
        return (0, 0, *frame_size)
    x, y = offset
    return (x, y, x + size[0], y + size[1])


def resolve_display(name=None):
    """An X display from Odd1's names: "user" (the person's screen, $DISPLAY or :0), "agent" (the agent desktop,
    $ODD1_AGENT_DISPLAY or :1), or an X display such as ":1"; empty means "user"."""
    if not name or name == "user":
        return os.environ.get("DISPLAY") or ":0"
    if name == "agent":
        return os.environ.get("ODD1_AGENT_DISPLAY") or ":1"
    if name.startswith(":"):
        return name
    raise ValueError(f'display must be "user", "agent" or an X display like ":1", not {name!r}')


def default_path(xid):
    folder = Path(tempfile.gettempdir()) / "odd1-any-app"
    folder.mkdir(exist_ok=True)
    return folder / f"window-{xid:x}-{time.strftime('%Y%m%d-%H%M%S')}.png"


def clients(display):
    """(xid, title, app) for every window the window manager manages, bottom to top."""
    from Xlib import X
    root = display.screen().root
    atom = display.intern_atom
    listed = root.get_full_property(atom("_NET_CLIENT_LIST_STACKING"), X.AnyPropertyType)
    found = []
    for xid in (listed.value if listed else []):
        window = display.create_resource_object("window", xid)
        name = window.get_full_property(atom("_NET_WM_NAME"), atom("UTF8_STRING"))
        title = name.value.decode("utf-8", "replace") if name else (window.get_wm_name() or "")
        app = (window.get_wm_class() or ("", ""))[1]
        found.append((xid, title if isinstance(title, str) else title.decode("latin-1"), app))
    return found


def capture(query, out=None, frame=False, settle=SETTLE_SECONDS, display_name=None):
    """Capture one window to a PNG; returns {path, window, title, app, width, height, sha256}. display_name: "user"
    (default), "agent" (the agent desktop) or an X display such as ":1"."""
    from PIL import Image
    from Xlib import X, display as xdisplay
    from Xlib.ext import composite

    display = xdisplay.Display(resolve_display(display_name))
    try:
        root = display.screen().root
        xid, title, app = match_window(clients(display), query)
        window = display.create_resource_object("window", xid)
        if window.get_attributes().map_state != X.IsViewable:
            raise RuntimeError(f"window {hex(xid)} is minimized or unmapped, so it has no pixels; restore it first")
        top = window
        while (parent := top.query_tree().parent).id != root.id:
            top = parent
        # Automatic redirects stack (ARGB windows are redirected already), so this never takes a window from
        # another client; the app repaints into its new pixmap on the expose that follows.
        composite.redirect_window(top, composite.RedirectAutomatic)
        display.sync()
        time.sleep(settle)
        pixmap = composite.name_window_pixmap(top)
        geometry, top_geometry = window.get_geometry(), top.get_geometry()
        image = pixmap.get_image(0, 0, top_geometry.width, top_geometry.height, X.ZPixmap, 0xFFFFFFFF)
        origin = window.translate_coords(top, 0, 0)
        box = client_box((-origin.x, -origin.y), (geometry.width, geometry.height),
                         (top_geometry.width, top_geometry.height), frame)
    finally:
        display.close()
    picture = Image.frombytes("RGB", (top_geometry.width, top_geometry.height), image.data, "raw", "BGRX").crop(box)
    path = Path(out) if out else default_path(xid)
    picture.save(path, "PNG")
    return {"path": str(path), "window": hex(xid), "title": title, "app": app, "width": picture.width,
            "height": picture.height, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="any-app screenshot", description=__doc__.splitlines()[0])
    parser.add_argument("window", help="title or app substring (must match exactly one window), or X11 id")
    parser.add_argument("-o", "--out", help="PNG path (default: a new file under $TMPDIR/odd1-any-app)")
    parser.add_argument("--frame", action="store_true", help="include the window manager's frame")
    parser.add_argument("--display", default="user", help='"user" (default), "agent" or an X display like ":1"')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(capture(args.window, args.out, args.frame, display_name=args.display)))
    except (LookupError, RuntimeError, ValueError) as exc:
        print(f"any-app screenshot: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
