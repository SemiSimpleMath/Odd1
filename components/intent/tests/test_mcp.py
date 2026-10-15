"""The agent entry point, end to end: a real MCP client plans, applies, lists and undoes a backup through the server."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

if not sys.platform.startswith("linux"):
    pytest.skip("Intent's engine is Linux-only (fcntl.flock, renameat2, /proc/self/fd); run on Odd1 or another Linux host",
                allow_module_level=True)
pytest.importorskip("mcp", reason="the MCP Python SDK (package 'mcp') is not installed")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

COMPONENT = Path(__file__).resolve().parent.parent


def _value(result):
    assert not result.isError, result.content
    text = result.content[0].text if result.content else ""
    try:
        return json.loads(text)
    except ValueError:
        return text


async def _run(base: Path) -> None:
    env = dict(os.environ, PYTHONPATH=str(COMPONENT), ODD1_INTENT_STATE=str(base / "state"))
    params = StdioServerParameters(command=sys.executable, args=["-m", "odd1_intent.mcp_server"], env=env)
    source = base / "notes.txt"
    source.write_text("exact bytes\n")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert {"intent_plan", "intent_apply", "intent_undo", "intent_history"} <= names
            plan = _value(await session.call_tool("intent_plan", {"request": f'Make a backup of "{source}"'}))
            assert [a["kind"] for a in plan["actions"]] == ["ADD"]
            assert not (base / "notes.txt.bak").exists()  # planning changes nothing
            entry = _value(await session.call_tool("intent_apply", {"plan_id": plan["plan_id"], "indexes": [0]}))
            assert (base / "notes.txt.bak").read_text() == "exact bytes\n"
            again = await session.call_tool("intent_apply", {"plan_id": plan["plan_id"], "indexes": [0]})
            assert again.isError  # a plan applies once
            listed = _value(await session.call_tool("intent_history", {}))
            listed = listed if isinstance(listed, list) else [listed]
            assert listed[0]["id"] == entry["id"]
            _value(await session.call_tool("intent_undo", {"transaction_id": entry["id"]}))
            assert not (base / "notes.txt.bak").exists()
            assert source.read_text() == "exact bytes\n"


def test_mcp_client_plans_applies_and_undoes():
    with tempfile.TemporaryDirectory(prefix="odd1-intent-mcp-") as td:
        asyncio.run(_run(Path(td)))
