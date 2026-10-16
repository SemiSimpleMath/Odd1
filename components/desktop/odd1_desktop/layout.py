"""The desktop's geometry and look, as pure functions (no display needed, so every rule is tested offline).

The screen: the chat column on the right (full height, its tabs at the top), the top bar (the panel) over the work
area only, the taskbar floating at the bottom of the work area, and the work area for apps on the left. Sizes follow the screen, which follows the
viewer's window.
"""
CHAT_TITLES = ("Claude Code",)
AGENT_DESKTOP_TITLE = "Agent desktop"  # the agent display's window (agent-desktop): one taskbar item for all of it
# The wallpaper: deep navy, lit by a few soft fields of colour, placed in the work area (fractions of it):
# ((x, y, radius), colour, strength).
WALLPAPER = ((22, 28, 48), (30, 36, 62))
WALLPAPER_FIELDS = (
    ((0.15, 0.95, 0.75), (38, 120, 140), 0.55),  # teal, low left
    ((0.80, 0.08, 0.65), (92, 70, 160), 0.50),  # violet, top right
    ((1.00, 0.85, 0.50), (200, 110, 90), 0.24),  # a warm glow, low right
    ((0.45, 0.45, 0.50), (60, 90, 170), 0.30),  # blue, centre
)
# The Odd1 owl in the middle of the work area, in its own colours dimmed into the night (65% brightness), 85%
# opaque, 39% of the screen's height.
WATERMARK_BRIGHTNESS, WATERMARK_OPACITY, WATERMARK_HEIGHT = 0.65, 0.85, 0.39
CHAT_BACKGROUND, CHAT_EDGE = (23, 24, 28), (44, 46, 54)  # the chat column and its 1px left edge
HEADER_HEIGHT = 34
HEADER_TEXT, HEADER_TEXT_DIM, HEADER_ACCENT = (228, 228, 232), (138, 140, 150), (217, 119, 87)
DOCK_SHARE, DOCK_MIN, DOCK_MAX = 0.36, 460, 680


def dock_width(screen_width):
    """The chat column's width: about a third of the screen, never cramped, never dominant."""
    return max(DOCK_MIN, min(DOCK_MAX, round(screen_width * DOCK_SHARE)))


def column(screen):
    """(x, y, width, height) of the whole chat column on a (width, height) screen: the full height, its tabs at the
    very top (the top bar spans only the work area)."""
    width = dock_width(screen[0])
    return (screen[0] - width, 0, width, screen[1])


def header_box(screen):
    x, y, width, _ = column(screen)
    return (x, y, width, HEADER_HEIGHT)


def chat_box(screen):
    """Where a chat window goes: the column below its header."""
    x, y, width, height = column(screen)
    return (x, y + HEADER_HEIGHT, width, height - HEADER_HEIGHT)


def work_area(screen, top, bottom=0):
    return (0, top, screen[0] - dock_width(screen[0]), screen[1] - top - bottom)


def panel_length(screen_width):
    """The top bar's length (xfce4-panel's percent of the screen width): exactly the work area, so its right end, the
    clock, sits at the work area's corner beside the chat column."""
    return 100 * (screen_width - dock_width(screen_width)) / screen_width


def strut(screen):
    """_NET_WM_STRUT_PARTIAL reserving the column on the right edge, top to bottom."""
    width = dock_width(screen[0])
    return [0, width, 0, 0, 0, 0, 0, screen[1] - 1, 0, 0, 0, 0]


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


def tab_labels(titles):
    """Labels for the chat tabs, from each chat's agent system ("Claude", "Task manager"): one chat of a system is
    just its title; several of one system are numbered in the order they opened."""
    total = {title: titles.count(title) for title in titles}
    seen, labels = {}, []
    for title in titles:
        seen[title] = seen.get(title, 0) + 1
        labels.append(title if total[title] == 1 else f"{title} {seen[title]}")
    return labels


# The agent systems a person can talk to (/etc/odd1/agents.json, nix/odd1.nix's odd1.agents): each chat's terminal
# carries the role "odd1-agent:<name>", so the column knows whose chat it is whatever title the agent sets.
AGENT_ROLE = "odd1-agent:"
MENU_WIDTH, MENU_ITEM, MENU_PAD = 340, 52, 6
MENU_BACKGROUND, MENU_HOVER = (32, 34, 41), (44, 47, 56)


def agent_name(role):
    """The agent system a chat window belongs to, from its WM_WINDOW_ROLE; None for other windows."""
    return role[len(AGENT_ROLE):] or None if role and role.startswith(AGENT_ROLE) else None


def menu_box(header, button, count):
    """(x, y, width, height) on the screen of the New chat menu: below the header, its right edge under the button's
    right edge, and never left of the column."""
    header_x, header_y, _, header_height = header
    button_x, button_width = button
    x = max(header_x + button_x + button_width - MENU_WIDTH, header_x + 4)
    return (x, header_y + header_height, MENU_WIDTH, count * MENU_ITEM + 2 * MENU_PAD)


def menu_hit(y, count):
    """Index of the menu item at y (inside the menu), or None."""
    index = (y - MENU_PAD) // MENU_ITEM
    return index if y >= MENU_PAD and index < count else None


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


def watermark_height(screen_height):
    return round(screen_height * WATERMARK_HEIGHT)


def ghost(mark):
    """The owl (RGBA) dimmed into the wallpaper: its own colours, darkened, partly see-through."""
    from PIL import Image, ImageEnhance
    dim = ImageEnhance.Brightness(mark.convert("RGB")).enhance(WATERMARK_BRIGHTNESS).convert("RGBA")
    dim.putalpha(Image.eval(mark.getchannel("A"), lambda v: round(v * WATERMARK_OPACITY)))
    return dim


def wallpaper(size, dock, mark=None):
    """The wallpaper for a (width, height) screen with a `dock`-wide chat column: the column is painted in, so it
    stays solid where a terminal stops short of the edge (terminals size in whole character cells). Computed at an
    eighth of the size and scaled up (the fields are soft anyway), with a little grain so it never bands. `mark`
    (the owl, RGBA) goes faintly in the middle of the work area, a little above centre."""
    import math
    import random
    from PIL import Image, ImageDraw, ImageFilter
    width, height = size
    area = max(width - dock, 1)
    small = (max(area // 8, 8), max(height // 8, 8))
    image = Image.new("RGB", small)
    pixels = image.load()
    for y in range(small[1]):
        t = y / (small[1] - 1)
        base = [a + (b - a) * t for a, b in zip(*WALLPAPER)]
        for x in range(small[0]):
            colour = list(base)
            for (fx, fy, radius), field, strength in WALLPAPER_FIELDS:
                near = max(0.0, 1 - math.hypot((x / small[0] - fx) * area / height, y / small[1] - fy) / radius)
                weight = strength * near * near * (3 - 2 * near)  # smoothstep falloff
                colour = [c + (f - c) * weight for c, f in zip(colour, field)]
            pixels[x, y] = tuple(round(c) for c in colour)
    image = image.filter(ImageFilter.GaussianBlur(2)).resize((area, height), Image.BICUBIC)
    grain = random.Random(area * height).randbytes(area * height)  # the same grain every time for a size
    image = Image.blend(image, Image.frombytes("L", (area, height), grain).convert("RGB"), 0.02)
    if mark is not None:
        faint = ghost(mark).convert("RGBA")
        image = image.convert("RGBA")
        image.alpha_composite(faint, ((area - faint.width) // 2, max((height - faint.height) // 2 - height // 36, 0)))
        image = image.convert("RGB")
    canvas = Image.new("RGB", (width, height), CHAT_BACKGROUND)
    canvas.paste(image, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.line([(width - dock - 1, 0), (width - dock - 1, height)], fill=CHAT_EDGE)
    return canvas


# The taskbar ------------------------------------------------------------------------------------------------------
# What is running, at the bottom of the work area: the person's apps (one item per app, however many windows), then
# one item for everything agents run (the agent desktop), then one item per chat in the column.
TASKBAR_ICON, TASKBAR_PAD, TASKBAR_GAP, TASKBAR_MARGIN = 40, 10, 8, 10
TASKBAR_HEIGHT = TASKBAR_ICON + 2 * TASKBAR_PAD + 4  # room for the running dot under the icons
TASKBAR_BACKGROUND, TASKBAR_ALPHA = (18, 20, 28), 0.72  # dark glass over the wallpaper
TASKBAR_SEPARATOR = 12  # the gap (with a hairline) between apps, agents and chats


def taskbar_items(apps, agents, chats, titles=None):
    """The taskbar's items, in order: [(kind, key, label)].
    apps: [(window id, app class, title)] of the person's windows, oldest first; one item per class, labelled with
    its newest window's title when it has one window, else with the class. agents: the agent desktop's window id
    (or None) and how many windows agents have open there. chats: one entry per chat window, in tab order, and
    titles: each chat's agent system ("Claude" for all when not given)."""
    groups = {}
    for xid, app, title in apps:
        groups.setdefault(app or title, []).append((xid, title))
    items = [("app", app, windows[-1][1] if len(windows) == 1 else app) for app, windows in groups.items()]
    agent_id, count = agents
    if agent_id is not None:
        items.append(("agents", agent_id, f"Agents · {count} app{'s' if count != 1 else ''}" if count else "Agents"))
    labels = tab_labels(titles or ["Claude"] * len(chats))
    items += [("chat", chat, label) for chat, label in zip(chats, labels)]
    return items


def taskbar_slots(items):
    """(x, width) of each item inside the taskbar, and the taskbar's whole width; a gap with a hairline separates
    the kinds."""
    slots, x, previous = [], TASKBAR_PAD, None
    for kind, _, _ in items:
        if previous is not None:
            x += TASKBAR_SEPARATOR if kind != previous else TASKBAR_GAP
        slots.append((x, TASKBAR_ICON))
        x += TASKBAR_ICON
        previous = kind
    return slots, max(x + TASKBAR_PAD, TASKBAR_ICON + 2 * TASKBAR_PAD)


def taskbar_box(screen, top, width):
    """(x, y, width, height) of the taskbar: centred at the bottom of the work area, floating a margin above it."""
    area_x, _, area_width, _ = work_area(screen, top)
    x = area_x + max((area_width - width) // 2, 0)
    return (x, screen[1] - TASKBAR_MARGIN - TASKBAR_HEIGHT, min(width, area_width), TASKBAR_HEIGHT)


def taskbar_reserved():
    """Height reserved at the bottom of the screen (bottom strut), so maximized windows stop above the taskbar."""
    return TASKBAR_HEIGHT + 2 * TASKBAR_MARGIN


def taskbar_strut(screen, top):
    x, _, width, _ = work_area(screen, top)
    return [0, 0, 0, taskbar_reserved(), 0, 0, 0, 0, 0, 0, x, x + width - 1]
