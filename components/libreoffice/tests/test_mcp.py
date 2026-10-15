"""The agent entry point, end to end: a real MCP client talks to the libreoffice MCP server and does the heights example."""
import asyncio
import csv
import json
import os
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _value(result):
    text = result.content[0].text if result.content else ""
    try:
        return json.loads(text)
    except ValueError:
        return text


async def _run(src: str) -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "odd1_office.mcp_server"], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert {"calc_open", "calc_read", "calc_write", "calc_formula", "calc_save", "calc_close"} <= names
            opened = _value(await session.call_tool("calc_open", {"path": src, "visible": False}))
            sheet = opened["sheets"][0]
            data = _value(await session.call_tool("calc_read", {"path": src}))["rows"]
            assert data[0] == ["name", "height_cm"] and len(data) == 5
            avg = _value(await session.call_tool("calc_formula", {
                "path": src, "sheet": "Summary", "cell": "A1",
                "formula": f'=AVERAGEIF(${sheet}.A2:A5;"Ana";${sheet}.B2:B5)'}))
            assert float(avg) == 171.5
            await session.call_tool("calc_close", {"path": src})


def test_mcp_client_drives_calc():
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "people.csv")
    with open(src, "w", newline="") as f:
        csv.writer(f).writerows([("name", "height_cm"), ("Ana", 170), ("Ben", 182), ("Ana", 173), ("Cleo", 165)])
    asyncio.run(_run(src))
