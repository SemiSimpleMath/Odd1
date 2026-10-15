"""The CLI and the MCP server (the agent entry point) expose the observation tools."""
import asyncio
import json
from unittest import mock

import pytest

from any_app import cli


def test_cli_lists_commands_without_arguments(capsys):
    assert cli.main([]) == 2
    assert "any-app ocr PNG" in capsys.readouterr().err


def test_cli_ocr_reports_unreadable_png(tmp_path, capsys):
    bad = tmp_path / "not.png"
    bad.write_bytes(b"not a png")
    assert cli.main(["ocr", str(bad)]) == 1
    assert "input must be a PNG" in capsys.readouterr().err


def test_mcp_server_offers_app_tools():
    pytest.importorskip("mcp", reason="the mcp package is installed on Odd1 by module.nix, not on this host")
    from any_app import mcp_server
    names = {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}
    assert {"app_describe", "app_ocr"} <= names


def test_mcp_describe_surfaces_describer_errors():
    pytest.importorskip("mcp", reason="the mcp package is installed on Odd1 by module.nix, not on this host")
    from any_app import mcp_server
    failed = mock.Mock(returncode=1, stdout=b"", stderr=b"no matching accessible window; found: Calc")
    with mock.patch.object(mcp_server.subprocess, "run", return_value=failed):
        with pytest.raises(RuntimeError, match="no matching accessible window"):
            mcp_server.describe("Nope")
    ok = mock.Mock(returncode=0, stdout=json.dumps({"source": "at-spi", "elements": []}).encode(), stderr=b"")
    with mock.patch.object(mcp_server.subprocess, "run", return_value=ok):
        assert mcp_server.describe("Calc")["source"] == "at-spi"
