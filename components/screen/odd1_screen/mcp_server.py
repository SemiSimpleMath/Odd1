"""The screen component's agent entry point: a desktop as one model, as MCP tools (stdio).

Two displays: "user" (the person's screen, window ids w1, w2, ...) and "agent" (the agent desktop, ids a1, a2, ...;
see the agent-desktop component), one model each, so element ids stay stable between calls and screen_changes
reports what changed since the previous view. Actions find their display from the id.
"""
import os

from mcp.server.fastmcp import FastMCP

from . import live, model

mcp = FastMCP("screen")
_screens = {"user": model.Screen("w", os.environ.get("DISPLAY") or ":0"),
            "agent": model.Screen("a", os.environ.get("ODD1_AGENT_DISPLAY") or ":1")}


def _screen(display: str) -> model.Screen:
    if display not in _screens:
        raise ValueError('display must be "user" or "agent"')
    return _screens[display]


def _owner(element_id: str) -> model.Screen:
    """The model an element id belongs to: "a3.e5" is the agent desktop's, "w3.e5" the person's."""
    return next((s for s in _screens.values() if element_id.startswith(s.prefix)), _screens["user"])


@mcp.tool()
def screen_view(window: str = "", display: str = "user", ocr: str = "auto", include_hidden: bool = False,
                max_elements: int = 400) -> dict:
    """A desktop as {screen, windows: [{id, title, box, active, minimized, source, calibration, elements: [{id,
    role, name, value?, box, states, verified?}]}]}, topmost window first; boxes are screen pixels [x, y, width,
    height]. display: "user" (the person's screen) or "agent" (the agent desktop, where agents run their own apps
    out of the person's way). window: title substring or window id; ocr: auto | always | never."""
    return live.observe(_screen(display), window=window, ocr=ocr, include_hidden=include_hidden,
                        max_elements=max_elements)


@mcp.tool()
def screen_changes(window: str = "", display: str = "user") -> dict:
    """What changed since the previous view of that display (windows opened/closed/changed, elements
    added/removed/changed); takes a fresh view without OCR."""
    screen = _screen(display)
    before = screen.last["view"] if screen.last else None
    after = live.observe(screen, window=window, ocr="never")
    if before and window:
        shown = {w["id"] for w in after["windows"]}
        before = {**before, "windows": [w for w in before["windows"] if w["id"] in shown]}
    return model.changes(before, after)


@mcp.tool()
def screen_at(x: int, y: int, display: str = "user") -> dict:
    """Which window and element own pixel (x, y) of that display, from a fresh view."""
    screen = _screen(display)
    live.observe(screen, ocr="never")
    return screen.at(x, y)


@mcp.tool()
def screen_find(text: str, role: str = "", display: str = "user") -> list:
    """Elements whose name contains text (case-insensitive), optionally of one role, from a fresh view."""
    view = live.observe(_screen(display))
    query = text.casefold()
    return [{"window": w["id"], **e} for w in view["windows"] for e in w["elements"]
            if query in e["name"].casefold() and (not role or e["role"] == role)][:100]


@mcp.tool()
def screen_click(id: str, via: str = "auto", button: int = 1, allow_unverified: bool = False,
                 person_approved: bool = False) -> dict:
    """Press an element ("w3.e12" on the person's screen, "a3.e12" on the agent desktop) and return what changed.
    via: "auto" (the app's own accessibility action when the element has one: works on minimized and covered
    windows and leaves the person's mouse alone; else the real pointer), "app", or "pointer" (that display's mouse
    at the element's centre; refused if minimized, covered or unverified). The agent desktop's pointer is its own.
    On the person's screen the pointer is theirs: ask them first, then pass person_approved=true."""
    return live.click(_owner(id), id, via=via, button=button, allow_unverified=allow_unverified,
                      person_approved=person_approved)


@mcp.tool()
def screen_set_text(id: str, text: str) -> dict:
    """Replace an entry's or text field's contents through the app itself (works minimized or covered; never
    password fields). Returns what changed."""
    return live.set_text(_owner(id), id, text)


@mcp.tool()
def screen_type(text: str, id: str = "", display: str = "", allow_unverified: bool = False,
                person_approved: bool = False) -> dict:
    """Type text with a display's keyboard into its focused window, clicking element id with the pointer first
    when given (the display then follows the id); stops if the focus moves to another window. On the person's
    screen the keyboard is theirs: prefer screen_set_text or the agent desktop; else ask them first, then pass
    person_approved=true."""
    screen = _owner(id) if id else _screen(display or "user")
    return live.type_text(screen, text, element_id=id, allow_unverified=allow_unverified,
                          person_approved=person_approved)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
