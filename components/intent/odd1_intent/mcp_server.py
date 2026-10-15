"""The intent component's agent entry point: plan, apply, undo and history as MCP tools (stdio).

Preview first, as in the CLI and GUI: intent_plan changes nothing and returns a plan_id with every considered action;
intent_apply applies only the action indexes the caller names, from a plan this server made, once. The engine
re-checks hashes and folder identities at apply time, so a stale plan is refused rather than guessed at.
"""
import uuid
from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from .engine import Plan, apply, history, parse, undo

mcp = FastMCP("intent")
_plans: dict[str, Plan] = {}
_MAX_PLANS = 32


@mcp.tool()
def intent_plan(request: str) -> dict:
    """Plan a file intent without changing anything. Supported wording: 'Update files in "<folder>" with the ones
    from "<folder>"' (newer and missing direct-child files) or 'Make a backup of "<file>"'. Returns plan_id and the
    actions (kind ADD/REPLACE/SKIP, source, target, reason, selected = the engine's default choice)."""
    plan = parse(request)
    plan_id = uuid.uuid4().hex
    _plans[plan_id] = plan
    while len(_plans) > _MAX_PLANS:
        _plans.pop(next(iter(_plans)))
    actions = [{"index": i, **{k: v for k, v in asdict(a).items() if not k.endswith("_parent_id")}}
               for i, a in enumerate(plan.actions)]
    return {"plan_id": plan_id, "intent": plan.intent, "mode": plan.mode, "actions": actions}


@mcp.tool()
def intent_apply(plan_id: str, indexes: list[int]) -> dict:
    """Apply exactly the listed ADD/REPLACE action indexes of a plan from intent_plan (each plan applies once).
    Show the person the actions first unless they already asked for exactly this. Returns the transaction
    (id, records with SHA-256 hashes); intent_undo reverses it."""
    plan = _plans.pop(plan_id, None)
    if plan is None:
        raise ValueError(f"Unknown or already used plan_id {plan_id!r}; call intent_plan again")
    return apply(plan, indexes)


@mcp.tool()
def intent_undo(transaction_id: str = "") -> dict:
    """Undo a transaction (the latest when empty): restore replaced files, remove added ones, verify hashes.
    Refused if the files changed since."""
    return undo(transaction_id or None)


@mcp.tool()
def intent_history() -> list[dict]:
    """Applied and undone transactions, newest first."""
    return history()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
