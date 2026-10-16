"""The two entry points: the odd1-tasks CLI, and a real MCP client against the tasks server."""
import asyncio
import json
import os
import sys

import pytest
from conftest import COMPONENT

from odd1_tasks import cli


def test_cli_create_approve_show(store, monkeypatch, capsys):
    assert cli.main(["create", "Free space", "--done-when", "true", "--tag", "housekeeping", "--resource", "/tmp"]) == 0
    tid = int(capsys.readouterr().out.split()[1])
    monkeypatch.setenv("ODD1_TASKS_AGENT", "housekeeper")
    assert cli.main(["approve", str(tid)]) == 1
    assert "may not decide" in capsys.readouterr().err
    monkeypatch.delenv("ODD1_TASKS_AGENT")
    assert cli.main(["approve", str(tid)]) == 0
    assert cli.main(["claim", "--as", "housekeeper"]) == 0
    assert cli.main(["log", str(tid), "half way"]) == 0
    cli.main(["show", str(tid)])
    out = capsys.readouterr().out
    assert "[claimed]" in out and "half way" in out and "person:tester" in out
    cli.main(["list", "--json", "--status", "claimed"])
    assert [t["id"] for t in json.loads(capsys.readouterr().out)] == [tid]


async def _mcp(state):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = dict(os.environ, ODD1_TASKS_STATE=state, PYTHONPATH=str(COMPONENT), ODD1_TASKS_AGENT="housekeeper")
    params = StdioServerParameters(command=sys.executable, args=["-m", "odd1_tasks.mcp_server"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert names == {f"tasks_{op}" for op in
                             ("create", "list", "show", "approve", "claim", "renew", "release", "log", "finish",
                              "decline", "cancel", "escalate", "attention", "changes", "ask", "inbox", "reply",
                              "close_by_check", "close", "reopen")}

            async def call(name, **args):
                return json.loads((await session.call_tool(name, args)).content[0].text)
            t = await call("tasks_create", goal="Delete old ISO", done_when="test ! -e /x.iso", tags=["needs-person"])
            assert t["status"] == "proposed" and t["created_by"] == "housekeeper"
            assert "may not decide" in (await call("tasks_approve", task_id=t["id"]))["error"]


def test_mcp_agent_proposes_but_cannot_approve(store):
    pytest.importorskip("mcp", reason="the mcp package is not installed here (it is on Odd1)")
    asyncio.run(_mcp(str(store.state)))
