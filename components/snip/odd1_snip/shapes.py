"""Pure rules of a snip: rectangles, which window is under the pointer, where a snip is saved."""
import datetime
import os
from pathlib import Path

MODES = ("rect", "free", "window", "full")


def rect(x0, y0, x1, y1):
    """The rectangle (x, y, w, h) spanned by two corners, dragged in any direction."""
    return min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)


def clip(r, width, height):
    """`r` cut to the screen (0, 0, width, height); None when nothing of it is left."""
    x, y, w, h = r
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1 - x0, y1 - y0


def bbox(points):
    """The rectangle around a freeform path's points."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return rect(min(xs), min(ys), max(xs) + 1, max(ys) + 1)


def big_enough(r, least=3):
    """A drag smaller than this is a click, not a snip."""
    return r is not None and r[2] >= least and r[3] >= least


def window_at(windows, x, y):
    """The topmost window rectangle containing (x, y); `windows` are (x, y, w, h), bottom to top."""
    for r in reversed(windows):
        if r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]:
            return r
    return None


def folder(home=None):
    """Where snips are saved: ~/Pictures/Screenshots, as on Windows ($XDG_PICTURES_DIR when set)."""
    pictures = os.environ.get("XDG_PICTURES_DIR") or str(Path(home or Path.home()) / "Pictures")
    return Path(pictures) / "Screenshots"


def filename(when=None):
    when = when or datetime.datetime.now()
    return when.strftime("Snip %Y-%m-%d %H%M%S.png")


def free_path(directory, name):
    """`directory/name`, or `name (2)`, `name (3)`, ... when it is taken: a snip never replaces another."""
    path = Path(directory) / name
    stem, suffix = path.stem, path.suffix
    n = 2
    while path.exists():
        path = Path(directory) / f"{stem} ({n}){suffix}"
        n += 1
    return path
