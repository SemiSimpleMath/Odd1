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
    """Approve a proposed task (or unblock a blocked one). A person may approve anything; an agent only tasks within
    the tags a person delegated to it (its role's `decides`), and never one it proposed itself."""
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


@mcp.tool()
def tasks_decline(task_id: int, reason: str) -> dict:
    """Turn a proposal down (it is closed, the reason kept in its log). Only within the tags a person delegated to
    you (your role's `decides`), never a task you proposed; otherwise escalate it."""
    return _do(Store().decline, task_id, actor_from_env(), reason)


@mcp.tool()
def tasks_cancel(task_id: int, reason: str) -> dict:
    """Cancel open work (ready, claimed, blocked, parked; a recurring task's series ends). A running agent is
    stopped at its runner's next lease renewal. Same authority as tasks_decline."""
    return _do(Store().cancel, task_id, actor_from_env(), reason)


@mcp.tool()
def tasks_escalate(task_id: int, reason: str) -> dict:
    """Hand a decision to a person: say what they must decide. It reaches them until a person acts on the task."""
    return _do(Store().escalate, task_id, actor_from_env(), reason)


@mcp.tool()
def tasks_attention(who: str = "") -> list[dict]:
    """Who has to act on what now: [{who, kind, task, text}]. who: "person", a role name (e.g. "manager"), or all."""
    from . import attention
    found = attention.items(Store())
    return attention.for_whom(found, who) if who else found


@mcp.tool()
def tasks_changes(since: int = 0) -> dict:
    """Everything that happened after log entry `since` (each with its task's status now), and the cursor to pass
    next time: catch up on what others did instead of rereading everything."""
    return Store().changes(since)


@mcp.tool()
def tasks_ask(text: str, task_id: int = 0) -> dict:
    """Ask the task manager a question (for a bug or a piece of work, file a ticket with tasks_create instead).
    task_id: the task it is about, if any. The answer arrives later in your own conversation as a message from
    the task manager ("@your-name ..."); a role agent whose run has ended gets it in its next run's prompt."""
    from . import inbox
    who = inbox.caller()
    return _do(Store().ask, who["name"], who["actor"], text, session=who["session"], task_id=task_id or None)


@mcp.tool()
def tasks_inbox(answered: bool = False) -> list[dict] | dict:
    """The task manager's inbox: unanswered messages (answered=true: all, with their answers). For the manager."""
    return _do(Store().inbox, answered)


@mcp.tool()
def tasks_reply(message_id: int, text: str) -> dict:
    """Answer one inbox message (the manager, or a person). Returns {reply, deliver: {to, message}}: when `to` is
    set, send `message` with SendMessage to `to` now, so it reaches the asker's conversation; when it is null the
    asker's session has ended, and a role agent reads the answer in its next run's prompt."""
    from . import inbox
    try:
        question, reply = Store().reply(message_id, actor_from_env(), text)
    except TaskError as e:
        return {"error": str(e)}
    return {"reply": reply, "deliver": inbox.delivery(question, reply)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
