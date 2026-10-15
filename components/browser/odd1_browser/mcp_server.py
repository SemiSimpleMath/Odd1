"""The browser component's agent entry point: web pages as MCP tools (stdio).

One Firefox session per server, started on the first browser_open and kept until browser_close or exit, so element
ids from a view stay valid for the next action. Every result is the page view (schema-v1.json, render result).
URLs are literal-loopback http only; form POSTs need allow_form_post=true on the call that submits.

    python -m odd1_browser.mcp_server [--visible]    --visible shows Firefox on the desktop instead of headless
"""
import argparse
import atexit

from mcp.server.fastmcp import FastMCP

from . import render, text

mcp = FastMCP("browser")
_visible = False
_session: render.FirefoxSession | None = None


def _browser() -> render.FirefoxSession:
    global _session
    if _session is None:
        _session = render.FirefoxSession(headless=not _visible)
    return _session


def _act(element_id: str, action: str, value: str | None = None, *, allow_form_post: bool = False,
         wait_ms: int = 500) -> dict:
    step = {"id": element_id, "action": action}
    if value is not None:
        step["value"] = value
    return _browser().act([step], allow_form_post=allow_form_post, wait_ms=wait_ms)


@mcp.tool()
def browser_open(url: str, wait_ms: int = 500) -> dict:
    """Open a loopback http URL (e.g. http://127.0.0.1:8000/) in Firefox, let its JavaScript run for wait_ms, and
    return the page view: title, text, headings, links, elements (interactive, with ids like "el:0003") and
    stable_ids ("id:<html id>")."""
    return _browser().open(url, wait_ms=wait_ms)


@mcp.tool()
def browser_view(screenshot: bool = False) -> dict:
    """Return the current page view again (after the page changed by itself); screenshot adds a base64 PNG."""
    return _browser().view(screenshot=screenshot)


@mcp.tool()
def browser_click(id: str, allow_form_post: bool = False, wait_ms: int = 500) -> dict:
    """Click an element by id ("el:0003" or "id:send") and return the resulting view. A click that submits a POST
    form needs allow_form_post=true."""
    return _act(id, "click", allow_form_post=allow_form_post, wait_ms=wait_ms)


@mcp.tool()
def browser_type(id: str, text: str) -> dict:
    """Set the text of an input or textarea by id (fires input and change events); returns the view."""
    return _act(id, "type", text)


@mcp.tool()
def browser_select(id: str, value: str) -> dict:
    """Choose an option of a select element by its option value; returns the view."""
    return _act(id, "select", value)


@mcp.tool()
def browser_submit(id: str, allow_form_post: bool = False, wait_ms: int = 500) -> dict:
    """Submit a form (by the form's id or one of its submit buttons) and return the resulting view. POST forms need
    allow_form_post=true: only that exact same-origin URL-encoded body is let through, once."""
    return _act(id, "submit", allow_form_post=allow_form_post, wait_ms=wait_ms)


@mcp.tool()
def browser_find(query: str, context: int = 60) -> dict:
    """Find a literal, case-insensitive string in the current page's text: offsets and excerpts."""
    return text.find_text(_browser().view()["page"], query, context=context)


@mcp.tool()
def browser_fetch(url: str) -> dict:
    """Fetch a loopback http URL without JavaScript or Firefox (fast): title, text, headings, links."""
    return text.browse(url)


@mcp.tool()
def browser_close() -> str:
    """Close Firefox. The next browser_open starts a fresh one."""
    global _session
    if _session is not None:
        session, _session = _session, None
        session.close()
    return "closed"


def _close_at_exit() -> None:
    if _session is not None:
        _session.close()


def main(argv=None) -> None:
    global _visible
    parser = argparse.ArgumentParser(prog="odd1-browser-mcp")
    parser.add_argument("--visible", action="store_true", help="show Firefox on the desktop (DISPLAY) instead of headless")
    args = parser.parse_args(argv)
    _visible = args.visible
    atexit.register(_close_at_exit)
    mcp.run()


if __name__ == "__main__":
    main()
