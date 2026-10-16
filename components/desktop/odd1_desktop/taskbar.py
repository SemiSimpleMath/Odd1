"""The taskbar: what is running, floating at the bottom of the work area.

The person's apps get one item each (all of an app's windows behind it); everything agents run on the agent
desktop (display :1) sits behind one Agents item with a count; each chat in the column gets its own item. Drawn
like the chat header (PIL into a plain X window); it fakes translucency by painting over the wallpaper it covers.
"""
import os

from . import layout

AGENT_DISPLAY = os.environ.get("ODD1_AGENT_DISPLAY") or ":1"
CHAT_COLOUR = (217, 119, 87)  # the chat accent (header underline)
AGENTS_COLOURS = ((96, 84, 206), (46, 132, 196))  # top-left to bottom-right
SCALE = 4  # icons and the bar's corners are drawn at 4x and scaled down: smooth edges


def rounded_mask(size, radius):
    from PIL import Image, ImageDraw
    big = Image.new("L", (size[0] * SCALE, size[1] * SCALE), 0)
    ImageDraw.Draw(big).rounded_rectangle([0, 0, big.width - 1, big.height - 1], radius=radius * SCALE, fill=255)
    return big.resize(size, Image.LANCZOS)


def tile(colours, size):
    """A rounded square icon background, a diagonal gradient between two colours."""
    from PIL import Image, ImageChops
    (r1, g1, b1), (r2, g2, b2) = colours
    vertical = Image.linear_gradient("L")
    gradient = ImageChops.add(vertical, vertical.rotate(90), scale=2).transpose(Image.FLIP_LEFT_RIGHT)
    gradient = gradient.resize((size, size))
    image = Image.composite(Image.new("RGB", (size, size), (r2, g2, b2)), Image.new("RGB", (size, size), (r1, g1, b1)),
                            gradient)
    image.putalpha(rounded_mask((size, size), size // 4))
    return image


def agents_icon(size):
    """Agents: a 2x2 grid of windows on a blue-violet tile (many apps behind one item)."""
    from PIL import Image, ImageDraw
    icon = tile(AGENTS_COLOURS, size * SCALE)
    draw = ImageDraw.Draw(icon)
    cell, gap = size * SCALE * 0.22, size * SCALE * 0.08
    start = (size * SCALE - 2 * cell - gap) / 2
    for row in range(2):
        for col in range(2):
            x, y = start + col * (cell + gap), start + row * (cell + gap)
            draw.rounded_rectangle([x, y, x + cell, y + cell], radius=cell * 0.25, fill=(255, 255, 255, 225))
    return icon.resize((size, size), Image.LANCZOS)


def chat_icon(size):
    """A chat: an eight-ray spark on the chat accent."""
    import math
    from PIL import Image, ImageDraw
    big = size * SCALE
    icon = tile((CHAT_COLOUR, (190, 92, 64)), big)
    draw = ImageDraw.Draw(icon)
    centre, reach = big / 2, big * 0.27
    for ray in range(8):
        angle = math.pi * ray / 4 + math.pi / 8
        draw.line([(centre, centre), (centre + reach * math.cos(angle), centre + reach * math.sin(angle))],
                  fill=(255, 255, 255), width=round(big * 0.075))
    draw.ellipse([centre - big * 0.06, centre - big * 0.06, centre + big * 0.06, centre + big * 0.06],
                 fill=(255, 255, 255))
    return icon.resize((size, size), Image.LANCZOS)


def letter_icon(label, size, font):
    """An app without an icon of its own: its initial on a neutral tile."""
    from PIL import ImageDraw
    icon = tile(((92, 98, 116), (64, 70, 86)), size)
    ImageDraw.Draw(icon).text((size / 2, size / 2), (label or "?")[:1].upper(), font=font, anchor="mm",
                              fill=(240, 240, 244))
    return icon


def net_wm_icon(values, size):
    """The best _NET_WM_ICON image for `size` (the smallest at least that big, else the largest), as RGBA."""
    from PIL import Image
    images, index = [], 0
    while index + 2 <= len(values):
        width, height = values[index], values[index + 1]
        if width <= 0 or height <= 0 or index + 2 + width * height > len(values):
            break
        images.append((width, height, index + 2))
        index += 2 + width * height
    if not images:
        return None
    big_enough = [image for image in images if image[0] >= size]
    width, height, start = min(big_enough) if big_enough else max(images)
    data = bytearray()
    for argb in values[start:start + width * height]:
        data += bytes(((argb >> 16) & 255, (argb >> 8) & 255, argb & 255, (argb >> 24) & 255))
    return Image.frombytes("RGBA", (width, height), bytes(data)).resize((size, size), Image.LANCZOS)


class Taskbar:
    def __init__(self, shell):
        X = shell.X
        self.shell, self.X = shell, X
        self.window = shell.root.create_window(
            0, 0, 1, 1, 0, X.CopyFromParent, X.InputOutput, X.CopyFromParent, background_pixel=0x12141C,
            event_mask=X.ExposureMask | X.ButtonPressMask | X.PointerMotionMask | X.LeaveWindowMask)
        self.window.set_wm_name("Odd1 taskbar")
        self.window.set_wm_class("odd1-desktop", "Odd1-desktop")
        self.gc = self.window.create_gc()
        self.tip = shell.root.create_window(
            0, 0, 1, 1, 0, X.CopyFromParent, X.InputOutput, X.CopyFromParent, background_pixel=0x12141C,
            override_redirect=True)
        self.tip_gc = self.tip.create_gc()
        self.label_font = shell.font
        from .shell import font
        self.badge_font = font(11, "Bold")
        self.items, self.slots, self.box = [], [], None
        self.groups = {}  # app class -> [window ids], oldest first
        self.icons = {}  # window id -> RGBA icon (or None: has none)
        self.agent_count, self.agent_display = 0, None
        self.hover = None
        self.mapped = False

    # What is running ---------------------------------------------------------------------------------------------
    def count_agent_windows(self):
        """How many windows agents have open on the agent desktop (0 when it is not running)."""
        from Xlib import display
        try:
            if self.agent_display is None:
                self.agent_display = display.Display(AGENT_DISPLAY)
                from .shell import silence
                silence(self.agent_display, AGENT_DISPLAY)  # a (re)started agent desktop rings again otherwise
            root = self.agent_display.screen().root
            atom = self.agent_display.intern_atom
            listed = root.get_full_property(atom("_NET_CLIENT_LIST"), self.X.AnyPropertyType)
            return len(listed.value) if listed else 0
        except Exception:
            self.agent_display = None
            return 0

    def update(self, apps, agent_window):
        """Rebuild the items: apps [(window, class, title)], the agent desktop's window (or None)."""
        self.groups = {}
        for window, app, title in apps:
            self.groups.setdefault(app or title, []).append(window)
        self.agent_window = agent_window
        self.agent_count = self.count_agent_windows()
        listed = [(window.id, app, title) for window, app, title in apps]
        self.items = layout.taskbar_items(listed, (agent_window.id if agent_window else None, self.agent_count),
                                          self.shell.chats, self.shell.chat_titles())
        self.slots, width = layout.taskbar_slots(self.items)
        box = layout.taskbar_box(self.shell.size, self.shell.top, width)
        if box != self.box:
            self.box = box
            self.shell.dock(self.window, box, layout.taskbar_strut(self.shell.size, self.shell.top))
        if not self.mapped:
            self.window.map()
            self.mapped = True
        self.draw()

    def icon(self, window, label):
        if window.id not in self.icons:
            try:
                prop = window.get_full_property(self.shell.atom("_NET_WM_ICON"), self.X.AnyPropertyType)
                self.icons[window.id] = net_wm_icon(list(prop.value), layout.TASKBAR_ICON) if prop else None
            except Exception:
                self.icons[window.id] = None
        return self.icons[window.id] or letter_icon(label, layout.TASKBAR_ICON, self.label_font)

    def forget_icon(self, xid):
        self.icons.pop(xid, None)

    # Drawing -----------------------------------------------------------------------------------------------------
    def state(self, window):
        try:
            prop = window.get_full_property(self.shell.atom("_NET_WM_STATE"), self.X.AnyPropertyType)
            return list(prop.value) if prop else []
        except Exception:
            return []

    def active(self):
        prop = self.shell.root.get_full_property(self.shell.atom("_NET_ACTIVE_WINDOW"), self.X.AnyPropertyType)
        return prop.value[0] if prop and len(prop.value) else 0

    def draw(self):
        from PIL import Image, ImageDraw
        if self.box is None or self.shell.wallpaper is None:
            return
        x0, y0, width, height = self.box
        under = self.shell.wallpaper.crop((x0, y0, x0 + width, y0 + height))
        glass = Image.blend(under, Image.new("RGB", (width, height), layout.TASKBAR_BACKGROUND), layout.TASKBAR_ALPHA)
        image = Image.composite(glass, under, rounded_mask((width, height), 16))
        draw = ImageDraw.Draw(image, "RGBA")
        draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=16, outline=(255, 255, 255, 22))
        active, hidden = self.active(), self.shell.atom("_NET_WM_STATE_HIDDEN")
        top = layout.TASKBAR_PAD
        previous = None
        for index, ((kind, key, label), (x, size)) in enumerate(zip(self.items, self.slots)):
            if previous is not None and kind != previous:
                middle = x - layout.TASKBAR_SEPARATOR // 2
                draw.line([(middle, top + 8), (middle, top + size - 8)], fill=(255, 255, 255, 40))
            previous = kind
            if index == self.hover:
                draw.rounded_rectangle([x - 4, top - 4, x + size + 3, top + size + 3], radius=11,
                                       fill=(255, 255, 255, 26))
            if kind == "app":
                windows = self.groups.get(key, [])
                icon = self.icon(windows[-1], label) if windows else letter_icon(label, size, self.label_font)
                focused = any(window.id == active for window in windows)
                running = True
            elif kind == "agents":
                icon = agents_icon(size)
                focused = key == active
                running = self.agent_window is not None and hidden not in self.state(self.agent_window)
            elif self.shell.chat_agents.get(key.id, "claude") == "claude":
                icon = chat_icon(size)
                focused = key is self.shell.shown
                running = True
            else:  # another agent system: its initial, like an app without an icon
                icon = letter_icon(label, size, self.label_font)
                focused = key is self.shell.shown
                running = True
            image.paste(icon, (x, top), icon)
            if kind == "agents" and self.agent_count:  # badges go over the icon
                self.badge(image, x + size - 6, top - 3, str(self.agent_count))
            if kind == "chat" and label[-1:].isdigit() and label.rsplit(" ", 1)[0] in self.shell.chat_titles():
                self.badge(image, x + size - 6, top + size - 12, label.rsplit(" ", 1)[1],
                           colour=(40, 42, 50))
            dot_y = top + size + 5
            if focused:
                draw.rounded_rectangle([x + size / 2 - 8, dot_y, x + size / 2 + 8, dot_y + 3], radius=2,
                                       fill=(255, 255, 255, 235))
            elif running:
                draw.ellipse([x + size / 2 - 2, dot_y, x + size / 2 + 1, dot_y + 3], fill=(255, 255, 255, 140))
        data = image.convert("RGB").tobytes("raw", "BGRX")
        self.window.put_image(self.gc, 0, 0, width, height, self.X.ZPixmap, 24, 0, data)

    def badge(self, image, x, y, text, colour=(232, 96, 72)):
        from PIL import ImageDraw
        draw = ImageDraw.Draw(image)
        width = max(16, round(draw.textlength(text, font=self.badge_font)) + 8)
        draw.rounded_rectangle([x - width / 2, y, x + width / 2, y + 15], radius=8, fill=colour,
                               outline=layout.TASKBAR_BACKGROUND)
        draw.text((x, y + 8), text, font=self.badge_font, anchor="mm", fill=(255, 255, 255))

    # Pointer -----------------------------------------------------------------------------------------------------
    def hit(self, x):
        return layout.hit(self.slots, x)

    def motion(self, x):
        index = self.hit(x)
        if index != self.hover:
            self.hover = index
            self.draw()
            self.show_tip(index)

    def leave(self):
        if self.hover is not None:
            self.hover = None
            self.draw()
        self.show_tip(None)

    def show_tip(self, index):
        from PIL import Image, ImageDraw
        if index is None or index >= len(self.items):
            self.tip.unmap()
            return
        label = self.items[index][2]
        measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        width, height = round(measure.textlength(label, font=self.label_font)) + 20, 26
        image = Image.new("RGB", (width, height), layout.TASKBAR_BACKGROUND)
        ImageDraw.Draw(image).text((width / 2, height / 2), label, font=self.label_font, anchor="mm",
                                   fill=layout.HEADER_TEXT)
        x0, y0, _, _ = self.box
        slot_x, size = self.slots[index]
        x = max(x0 + slot_x + size // 2 - width // 2, 0)
        self.tip.configure(x=x, y=y0 - height - 6, width=width, height=height, stack_mode=self.X.Above)
        self.tip.map()
        self.tip.put_image(self.tip_gc, 0, 0, width, height, self.X.ZPixmap, 24, 0,
                           image.tobytes("raw", "BGRX"))

    def click(self, x):
        """What a click on an item does: an app comes forward (or, if it has the focus, minimizes); Agents shows the
        agent desktop (or hides it again); a chat comes up in the column."""
        import subprocess
        index = self.hit(x)
        if index is None:
            return
        self.show_tip(None)
        kind, key, _ = self.items[index]
        active = self.active()
        if kind == "app":
            windows = self.groups.get(key, [])
            focused = [window for window in windows if window.id == active]
            if focused:
                self.shell.minimize(focused[0])
            elif windows:
                self.shell.activate(self.shell.topmost(windows))
        elif kind == "agents":
            shown = self.agent_window is not None and self.shell.atom("_NET_WM_STATE_HIDDEN") not in self.state(
                self.agent_window)
            verb = "hide" if shown and key == active else "show"
            subprocess.Popen(["odd1-agent-desktop", verb], start_new_session=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            self.shell.show(key)
