"""odd1-agents: the Agents app, and its overview for scripts and agents.

    odd1-agents                  open the Agents window (Overview, Tasks, Roles, Claude Code)
    odd1-agents status [--json]  the overview: each role, what waits for the person, today, the machine
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="odd1-agents", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="app", choices=["app", "status"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
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
