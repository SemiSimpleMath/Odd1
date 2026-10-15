"""The agent desktop's commands, offline: how apps are started, which windows are new, what may be stopped."""
import asyncio
from unittest import mock

import pytest

from odd1_agent_desktop import cli, desktop


def test_apps_start_in_the_agent_slice_on_the_agent_display_with_accessibility():
    with mock.patch.object(desktop.shutil, "which", return_value="/bin/gnome-calculator"):
        args = desktop.run_args(["gnome-calculator", "--mode", "basic"], "odd1-agent-app-x")
    assert args[:6] == ["systemd-run", "--user", "--slice=odd1-agent.slice", "--unit=odd1-agent-app-x",
                        "--collect", "--quiet"]
    assert "--setenv=DISPLAY=:1" in args and "--setenv=GNOME_ACCESSIBILITY=1" in args
    assert args[args.index("--"):] == ["--", "/bin/gnome-calculator", "--mode", "basic"]


def test_wmctrl_listing():
    text = "0x00400004  0 32816  odd1 Calculator\n0x00600001 -1 0 N/A tint2\nnoise\n"
    assert desktop.parse_wmctrl(text) == [{"xid": "0x00400004", "pid": 32816, "title": "Calculator"},
                                          {"xid": "0x00600001", "pid": 0, "title": "tint2"}]


def test_run_reports_the_new_window(monkeypatch):
    listings = iter([[{"xid": "0x1", "pid": 1, "title": "old"}], [{"xid": "0x1", "pid": 1, "title": "old"}],
                     [{"xid": "0x1", "pid": 1, "title": "old"}, {"xid": "0x2", "pid": 7, "title": "Calculator"}]])
    monkeypatch.setattr(desktop, "windows", lambda: next(listings))
    monkeypatch.setattr(desktop, "running", lambda: True)
    monkeypatch.setattr(desktop.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    with mock.patch.object(desktop.subprocess, "run") as run:
        result = desktop.run("gnome-calculator")
    assert run.call_args[0][0][0] == "systemd-run"
    assert result["unit"].startswith("odd1-agent-app-gnome-calculator-") and result["unit"].endswith(".service")
    assert result["windows"] == [{"xid": "0x2", "pid": 7, "title": "Calculator"}]


def test_run_refuses_unknown_programs_and_a_stopped_display(monkeypatch):
    with pytest.raises(FileNotFoundError):
        desktop.run("no-such-program-odd1")
    monkeypatch.setattr(desktop, "running", lambda: False)
    with pytest.raises(RuntimeError, match="not running"):
        desktop.run("sh")


def test_shown_desktop_fits_the_persons_work_area():
    assert desktop.fit((1280, 720), (1920, 1040)) is None
    assert desktop.fit((1280, 720), (1024, 740)) == (1016, 708)
    # A chat column docked on the right leaves less width than the screen has.
    area = desktop.parse_workarea("_NET_WORKAREA(CARDINAL) = 0, 32, 1340, 868, 0, 32, 1340, 868")
    assert area == (1340, 868) and desktop.fit((1280, 720), area) is None
    assert desktop.parse_workarea("_NET_WORKAREA:  not found.") is None


def test_only_agent_apps_can_be_stopped():
    with pytest.raises(PermissionError):
        desktop.stop("odd1-taskbar.service")


def test_cli_usage_and_mcp_tools():
    assert cli.main([]) == 2
    pytest.importorskip("mcp", reason="the mcp package is installed on Odd1 by module.nix, not on this host")
    from odd1_agent_desktop import mcp_server
    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert names == {"desktop_run", "desktop_status", "desktop_stop", "desktop_show", "desktop_hide"}
