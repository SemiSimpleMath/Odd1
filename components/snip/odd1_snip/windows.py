"""The windows on the screen, bottom to top, as screen rectangles with their frames (for Window snips)."""
from Xlib import X, display as xdisplay
from Xlib.error import XError


def stacking(name=None):
    """[(x, y, w, h)] of the visible, normal windows, bottom to top; the desktop itself is left out."""
    d = xdisplay.Display(name)
    try:
        root = d.screen().root
        atom = d.intern_atom
        prop = root.get_full_property(atom("_NET_CLIENT_LIST_STACKING"), X.AnyPropertyType)
        hidden, desktop = atom("_NET_WM_STATE_HIDDEN"), atom("_NET_WM_WINDOW_TYPE_DESKTOP")
        out = []
        for wid in (prop.value if prop else []):
            w = d.create_resource_object("window", wid)
            try:
                state = w.get_full_property(atom("_NET_WM_STATE"), X.AnyPropertyType)
                kind = w.get_full_property(atom("_NET_WM_WINDOW_TYPE"), X.AnyPropertyType)
                if (state and hidden in state.value) or (kind and desktop in kind.value):
                    continue
                if w.get_attributes().map_state != X.IsViewable:
                    continue
                g = w.get_geometry()
                pos = w.translate_coords(root, 0, 0)
                ext = w.get_full_property(atom("_NET_FRAME_EXTENTS"), X.AnyPropertyType)
                left, right, top, bottom = ext.value if ext and len(ext.value) == 4 else (0, 0, 0, 0)
                out.append((-pos.x - left, -pos.y - top, g.width + left + right, g.height + top + bottom))
            except XError:
                continue  # closed while we looked
        return out
    finally:
        d.close()
