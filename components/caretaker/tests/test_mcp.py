"""The agent entry point end to end: a real MCP client lists and calls the caretaker's tools."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="the mcp package is not installed here (it is on Odd1)")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

COMPONENT = str(Path(__file__).resolve().parent.parent)


async def _run(state):
    env = dict(os.environ, ODD1_CARETAKER_STATE=state, PYTHONPATH=COMPONENT)
    params = StdioServerParameters(command=sys.executable, args=["-m", "odd1_caretaker.mcp_server"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert {"caretaker_status", "caretaker_pressure", "caretaker_repair", "caretaker_health"} <= names
            result = await session.call_tool("caretaker_status", {})
            data = json.loads(result.content[0].text)
            assert data["caretaker"]["state"] == "unknown"  # nothing recorded in a fresh state directory


def test_mcp_client_reads_status(tmp_path):
    asyncio.run(_run(str(tmp_path)))
