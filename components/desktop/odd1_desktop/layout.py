"""The desktop's geometry and look, as pure functions (no display needed, so every rule is tested offline).

The screen: a top bar (the panel), the chat column on the right (full height below the bar), and the work area
for apps on the left. Sizes follow the screen, which follows the viewer's window.
"""
CHAT_TITLES = ("Claude Code",)
WALLPAPER = ((233, 237, 242), (201, 211, 223))  # top, bottom: a calm light gradient
CHAT_BACKGROUND, CHAT_EDGE = (23, 24, 28), (44, 46, 54)  # the chat column and its 1px left edge
HEADER_HEIGHT = 34
HEADER_TEXT, HEADER_TEXT_DIM, HEADER_ACCENT = (228, 228, 232), (138, 140, 150), (217, 119, 87)
DOCK_SHARE, DOCK_MIN, DOCK_MAX = 0.36, 460, 680


def dock_width(screen_width):
    """The chat column's width: about a third of the screen, never cramped, never dominant."""
    return max(DOCK_MIN, min(DOCK_MAX, round(screen_width * DOCK_SHARE)))


def column(screen, top):
    """(x, y, width, height) of the whole chat column on a (width, height) screen below a `top`-high bar."""
    width = dock_width(screen[0])
    return (screen[0] - width, top, width, screen[1] - top)


def header_box(screen, top):
    x, y, width, _ = column(screen, top)
    return (x, y, width, HEADER_HEIGHT)


def chat_box(screen, top):
    """Where a chat window goes: the column below its header."""
    x, y, width, height = column(screen, top)
    return (x, y + HEADER_HEIGHT, width, height - HEADER_HEIGHT)


def work_area(screen, top):
    return (0, top, screen[0] - dock_width(screen[0]), screen[1] - top)


def strut(screen, top):
    """_NET_WM_STRUT_PARTIAL reserving the column on the right edge."""
    width = dock_width(screen[0])
    return [0, width, 0, 0, 0, 0, top, screen[1] - 1, 0, 0, 0, 0]


def fit(box, area, gap=12):
    """Move (and if need be shrink) an outer box (x, y, width, height) into a work area; None if it fits already.
    A window that had to move keeps a small gap from the area's edges."""
    x, y, width, height = box
    ax, ay, aw, ah = area
    if x >= ax and y >= ay and x + width <= ax + aw and y + height <= ay + ah:
        return None
    width, height = min(width, aw - 2 * gap), min(height, ah - 2 * gap)
    x = min(max(x, ax + gap), ax + aw - gap - width)
    y = min(max(y, ay + gap), ay + ah - gap - height)
    return x, y, width, height


def is_chat(title, titles=CHAT_TITLES):
    return title in titles


def tab_labels(count):
    """Labels for the chat tabs: one chat is just "Claude"; several are numbered in the order they opened."""
    return ["Claude"] if count == 1 else [f"Claude {n}" for n in range(1, count + 1)]


def tab_boxes(labels, measure, start=14, pad=12, gap=4):
    """(x, width) of each tab, given a text-width function; tabs sit left to right from `start`."""
    boxes, x = [], start
    for label in labels:
        width = measure(label) + 2 * pad
        boxes.append((x, width))
        x += width + gap
    return boxes


def hit(boxes, x):
    """Index of the tab under x, or None."""
    for index, (left, width) in enumerate(boxes):
        if left <= x < left + width:
            return index
    return None


def wallpaper(size, dock):
    """The wallpaper for a (width, height) screen with a `dock`-wide chat column: the column is painted in, so it
    stays solid where a terminal stops short of the edge (terminals size in whole character cells)."""
    from PIL import Image, ImageDraw
    width, height = size
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    top, bottom = WALLPAPER
    for y in range(height):
        t = y / max(height - 1, 1)
        draw.line([(0, y), (width, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    draw.rectangle([width - dock, 0, width, height], fill=CHAT_BACKGROUND)
    draw.line([(width - dock - 1, 0), (width - dock - 1, height)], fill=CHAT_EDGE)
    return image
