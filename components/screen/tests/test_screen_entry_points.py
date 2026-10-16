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
        result = live.click(screen, seven, via="pointer", wait_ms=0, person_approved=True)
    assert run.call_args[0][0][:4] == ["xdotool", "mousemove", "--sync", "555"]
    assert result["clicked"] == {"id": seven, "via": "pointer", "x": 555, "y": 420}


def test_the_persons_pointer_and_keyboard_need_their_approval_the_agent_desktops_do_not():
    x, atspi, words = calculator()
    person = model.Screen("w")
    seven = next(e["id"] for e in person.fuse(x, atspi, {"0x2": words})["windows"][0]["elements"] if e["name"] == "Seven")
    asked = []

    def decline(display, xid, title, what, reason=""):
        asked.append(what)
        raise PermissionError("the person declined")

    with mock.patch.object(live.subprocess, "run") as run, mock.patch.object(live.consent, "require", decline), \
            mock.patch.object(live, "observe", side_effect=lambda s, **_: s.fuse(x, atspi, {})):
        with pytest.raises(PermissionError, match="declined"):
            live.click(person, seven, via="pointer")
        with pytest.raises(PermissionError, match="declined"):
            live.type_text(person, "hello")
    assert not run.called  # nothing moved, nothing typed
    assert [what.split()[0] for what in asked] == ["click", "type"]  # the dialog asked them each time
    agent = model.Screen("a")
    seven = next(e["id"] for e in agent.fuse(x, atspi, {"0x2": words})["windows"][0]["elements"] if e["name"] == "Seven")
    with mock.patch.object(live.subprocess, "run") as run, \
            mock.patch.object(live, "observe", side_effect=lambda s, **_: s.fuse(x, atspi, {})):
        live.click(agent, seven, via="pointer", wait_ms=0)
    assert run.call_args[0][0][:2] == ["xdotool", "mousemove"]


def typing(focus):
    """A fake subprocess.run: focus queries answer from `focus` (a list of window ids, or "" for none), key
    presses are recorded."""
    keys = []

    def run(args, **_):
        if args[:2] == ["xdotool", "getactivewindow"]:
            return mock.Mock(stdout=focus.pop(0) + "\n" if focus else "0x2\n")
        if args[:2] == ["xdotool", "getwindowfocus"]:
            return mock.Mock(stdout="")
        assert args[:2] == ["xdotool", "key"] and "--clearmodifiers" in args
        keys.extend(args[args.index("--delay") + 2:])
        return mock.Mock(stdout="")
    return run, keys


def untype(keys):
    """xdotool key names -> the text they type."""
    names = {name: char for char, name in live.KEY_NAMES.items()}
    return "".join(names.get(k) or (k[6:].upper() if k.startswith("shift+") else
                                    chr(int(k[1:], 16)) if k.startswith("U") else k) for k in keys)


def test_type_lossless_names_every_character():
    text = "Hello, World!\nSecond LINE\twith 12 äö € ~`'\"\\ <tag> {x: [1]}; 50% & #3 @ ^ | ?\r\nend"
    keys = live.keystrokes(text)
    assert untype(keys) == text.replace("\r\n", "\n")
    assert "Return" in keys and "Tab" in keys and "shift+h" in keys and "U00E4" in keys
    with pytest.raises(ValueError, match="control character"):
        live.keystrokes("bell\a")


def test_type_lossless_sends_all_keys_in_chunks():
    x, atspi, words = calculator()
    agent = model.Screen("a")
    agent.fuse(x, atspi, {"0x2": words})
    text = "Line ONE\nline two: MiXeD case, 100% ✓\n" * 3
    run, keys = typing([])
    with mock.patch.object(live.subprocess, "run", side_effect=run), \
            mock.patch.object(live, "observe", side_effect=lambda s, **_: s.fuse(x, atspi, {})):
        assert live.type_text(agent, text, wait_ms=0)["typed"] == len(text)
    assert untype(keys) == text


def test_type_focus_guard_stops_when_the_focus_moves_to_another_window():
    x, atspi, words = calculator()
    agent = model.Screen("a")
    agent.fuse(x, atspi, {"0x2": words})
    run, keys = typing(["0x2", "0x2", "0x2", "0x9", "0x9"])  # someone takes the focus after the second chunk
    with mock.patch.object(live.subprocess, "run", side_effect=run):
        with pytest.raises(RuntimeError, match=f"stopped after {2 * live.TYPE_CHUNK} of"):
            live.type_text(agent, "x" * (5 * live.TYPE_CHUNK), wait_ms=0)
    assert "".join(keys) == "x" * (2 * live.TYPE_CHUNK)


def test_type_focus_guard_reports_a_move_during_the_last_chunk():
    x, atspi, words = calculator()
    agent = model.Screen("a")
    agent.fuse(x, atspi, {"0x2": words})
    run, keys = typing(["0x2", "0x2", "0x9"])
    with mock.patch.object(live.subprocess, "run", side_effect=run):
        with pytest.raises(RuntimeError, match="last 5 characters"):
            live.type_text(agent, "hello", wait_ms=0)


def test_type_focus_guard_refuses_without_a_known_focus():
    x, atspi, words = calculator()
    agent = model.Screen("a")
    agent.fuse(x, atspi, {"0x2": words})
    run, keys = typing(["", ""])  # no window manager answer and no input focus: the guard could not work
    with mock.patch.object(live.subprocess, "run", side_effect=run):
        with pytest.raises(RuntimeError, match="no window has the keyboard focus"):
            live.type_text(agent, "hello", wait_ms=0)
    assert keys == []
