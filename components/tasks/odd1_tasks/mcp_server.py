"""The tasks agent entry point (MCP, stdio): the same operations as the odd1-tasks CLI.

The caller is $ODD1_TASKS_AGENT when the runner started it for a role agent, otherwise the person's own agent.
Agents propose; only a person approves. Errors come back as {"error": "..."} naming the rule that refused.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .store import Store, TaskError, actor_from_env

mcp = FastMCP("tasks")


def _do(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except TaskError as e:
        return {"error": str(e)}


@mcp.tool()
def tasks_create(goal: str, done_when: str, tags: list[str], priority: int = 0, max_turns: int = 0,
                 max_minutes: float = 0, depends_on: list[int] = [], resources: list[str] = [],
                 schedule: str = "") -> dict:
    """Propose a task. goal: one sentence, the outcome. done_when: a shell command whose exit 0 means done (the
    runner runs it). tags: which role may claim it. resources: paths or services it changes. schedule: '1d' etc. for
    a recurring task. It starts as proposed and runs only after a person approves it."""
    return _do(Store().create, goal, done_when, tags, actor_from_env(), priority=priority,
               max_turns=max_turns or None, max_minutes=max_minutes or None, depends_on=depends_on,
               resources=resources, schedule=schedule or None)


@mcp.tool()
def tasks_list(status: str = "", tag: str = "") -> list[dict] | dict:
    """Tasks, optionally only one status (proposed/ready/claimed/blocked/verifying/done/parked) or tag."""
    return _do(Store().list, status or None, tag or None)


@mcp.tool()
def tasks_show(task_id: int) -> dict:
    """One task with its whole log (claims, notes, runs, checks, results)."""
    return _do(Store().show, task_id)


@mcp.tool()
def tasks_approve(task_id: int) -> dict:
    """A person approves a proposed task (or unblocks a blocked one). Call only when the person asked for this ID;
    role agents are refused."""
    return _do(Store().approve, task_id, actor_from_env())


@mcp.tool()
def tasks_claim(agent: str = "", task_id: int = 0) -> dict:
    """Claim task_id, or the best ready task the agent holds every tag for. A claim is a lease (10 min)."""
    return _do(Store().claim, agent or actor_from_env(), task_id or None) or {"claimed": None}


@mcp.tool()
def tasks_renew(task_id: int) -> dict:
    """Extend your claim's lease."""
    return _do(Store().renew, task_id, actor_from_env())


@mcp.tool()
def tasks_release(task_id: int, blocked: str = "") -> dict:
    """Give your claimed task back (ready), or with `blocked` set to the reason, mark it blocked for a person."""
    return _do(Store().release, task_id, actor_from_env(), blocked)


@mcp.tool()
def tasks_log(task_id: int, text: str) -> dict:
    """Append a line to your claimed task's log (what you did, what you found, what is left)."""
    return _do(Store().log, task_id, actor_from_env(), text) or {"ok": True}


@mcp.tool()
def tasks_finish(task_id: int, note: str = "") -> dict:
    """Say your claimed task is finished. It moves to verifying; the runner then runs done_when to decide."""
    return _do(Store().finish, task_id, actor_from_env(), note)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
