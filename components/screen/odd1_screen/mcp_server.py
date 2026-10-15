"""The screen component's agent entry point: the whole desktop as one model, as MCP tools (stdio).

One Screen per server, so element ids stay stable between calls and screen_changes reports what changed since the
previous view.
"""
from mcp.server.fastmcp import FastMCP

from . import live, model

mcp = FastMCP("screen")
_screen = model.Screen()


@mcp.tool()
def screen_view(window: str = "", ocr: str = "auto", include_hidden: bool = False, max_elements: int = 400) -> dict:
    """The desktop as {screen, windows: [{id, title, box, active, source, calibration, elements: [{id, role,
    name, box, states, verified?}]}]}, topmost window first; boxes are screen pixels [x, y, width, height].
    window: title substring or "w3" to restrict; ocr: auto | always | never (always re-verifies coordinates)."""
    return live.observe(_screen, window=window, ocr=ocr, include_hidden=include_hidden, max_elements=max_elements)


@mcp.tool()
def screen_changes(window: str = "") -> dict:
    """What changed since the previous view (windows opened/closed/changed, elements added/removed/changed);
    takes a fresh view without OCR."""
    before = _screen.last["view"] if _screen.last else None
    after = live.observe(_screen, window=window, ocr="never")
    if before and window:
        shown = {w["id"] for w in after["windows"]}
        before = {**before, "windows": [w for w in before["windows"] if w["id"] in shown]}
    return model.changes(before, after)


@mcp.tool()
def screen_at(x: int, y: int) -> dict:
    """Which window and element own screen pixel (x, y), from a fresh view."""
    live.observe(_screen, ocr="never")
    return _screen.at(x, y)


@mcp.tool()
def screen_find(text: str, role: str = "") -> list:
    """Elements whose name contains text (case-insensitive), optionally of one role, from a fresh view."""
    view = live.observe(_screen)
    query = text.casefold()
    return [{"window": w["id"], **e} for w in view["windows"] for e in w["elements"]
            if query in e["name"].casefold() and (not role or e["role"] == role)][:100]


@mcp.tool()
def screen_click(id: str, button: int = 1, allow_unverified: bool = False) -> dict:
    """Click an element ("w3.e12") at its centre with the real mouse; refused if its box is unverified or another
    window covers it. Returns what changed."""
    return live.click(_screen, id, button=button, allow_unverified=allow_unverified)


@mcp.tool()
def screen_type(text: str, id: str = "", allow_unverified: bool = False) -> dict:
    """Type text with the real keyboard into the focused window, clicking element id first when given."""
    return live.type_text(_screen, text, element_id=id, allow_unverified=allow_unverified)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
