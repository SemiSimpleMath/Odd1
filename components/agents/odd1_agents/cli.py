"""odd1-agents: the Agents app, and its overview for scripts and agents.

    odd1-agents                  open the Agents window (Overview, Tasks, Roles, Claude Code)
    odd1-agents status [--json]  the overview: each role, what waits for the person, today, the machine
    odd1-agents guard            Claude Code's PreToolUse hook: chats change the repository in their own worktree
    odd1-agents worktree SESSION this chat's worktree (made from main if missing); `tidy` removes landed ones
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="odd1-agents", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="app", choices=["app", "status", "guard", "worktree"])
    ap.add_argument("session", nargs="?", help="worktree: a chat's session id, or `tidy`")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.command == "guard":
        from . import worktree
        return worktree.guard(sys.stdin, sys.stdout)
    if a.command == "worktree":
        from . import worktree
        if not a.session:
            ap.error("worktree needs a session id (or tidy)")
        print("\n".join(worktree.tidy()) if a.session == "tidy" else worktree.prepare(a.session))
        return 0
    if a.command == "app":
        from . import app
        return app.main()
    from odd1_tasks.store import Store
    from . import model
    view = model.overview(Store())
    if a.json:
        print(json.dumps(view, indent=2))
        return 0
    print(view["line"])
    for r in view["roles"]:
        state = "on" if r["enabled"] else "paused"
        print(f"  {r['name']}: {state}, runner {r['runner']}, {r['runs_today']}/{r['runs_per_day']} runs today, "
              f"${r['cost_today']:.2f}")
    m = view["machine"]
    print(f"  machine: {m['level']} ({m['state']})" + "".join(f"\n    {p}" for p in m["problems"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
