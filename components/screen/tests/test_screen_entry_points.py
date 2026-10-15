"""The MCP server (the agent entry point) offers the screen tools; clicks refuse unverified or covered targets."""
import asyncio
from unittest import mock

import pytest

from odd1_screen import live, model
from test_model import calculator, word


def test_mcp_server_offers_screen_tools():
    pytest.importorskip("mcp", reason="the mcp package is installed on Odd1 by module.nix, not on this host")
    from odd1_screen import mcp_server
    names = {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}
    assert {"screen_view", "screen_changes", "screen_at", "screen_find", "screen_click", "screen_set_text", "screen_type"} <= names


def test_click_refuses_unverified_contradicted_and_covered_targets():
    x, atspi, words = calculator()
    screen = model.Screen()
    calc = screen.fuse(x, atspi, {})["windows"][0]
    seven = next(e["id"] for e in calc["elements"] if e["name"] == "Seven")
    with pytest.raises(PermissionError, match="unverified"):
        live.click(screen, seven, via="pointer")
    x["windows"].append({"xid": "0x9", "title": "On top", "pid": 7, "states": [],
                         "geometry": {"x": 500, "y": 380, "width": 300, "height": 100, "viewable": True}})
    screen.fuse(x, atspi, {"0x2": words})
    with pytest.raises(PermissionError, match="covered by 'On top'"):
        live.click(screen, seven, via="pointer")
    atspi["apps"][0]["frames"][0]["elements"].append(
        {"id": "9", "role": "push button", "name": "Undo", "bounds": {"x": 0, "y": 0, "width": 80, "height": 30},
         "window_bounds": {"x": 0, "y": -5, "width": 80, "height": 30}, "states": [], "visibility": "showing"})
    undo = next(e["id"] for e in screen.fuse(x, atspi, {"0x2": words + [word("Undo", 40, 60)]})["windows"][1]["elements"]
                if e["name"] == "Undo")
    with pytest.raises(PermissionError, match="outside its reported box"):
        live.click(screen, undo, via="pointer")


def test_click_moves_the_real_mouse_to_the_centre():
    x, atspi, words = calculator()
    screen = model.Screen()
    calc = screen.fuse(x, atspi, {"0x2": words})["windows"][0]
    seven = next(e["id"] for e in calc["elements"] if e["name"] == "Seven")
    with mock.patch.object(live.subprocess, "run") as run, \
            mock.patch.object(live, "observe", side_effect=lambda s, **_: s.fuse(x, atspi, {})):
        result = live.click(screen, seven, via="pointer", wait_ms=0)
    assert run.call_args[0][0][:4] == ["xdotool", "mousemove", "--sync", "555"]
    assert result["clicked"] == {"id": seven, "via": "pointer", "x": 555, "y": 420}
