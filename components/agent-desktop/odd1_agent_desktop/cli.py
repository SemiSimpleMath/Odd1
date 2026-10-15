"""odd1-agent-desktop: run programs on the agent desktop (out of the person's way), and show or hide it.

    odd1-agent-desktop run COMMAND...      start a program there; prints {unit, windows}
    odd1-agent-desktop status              display, host window, windows and running apps
    odd1-agent-desktop stop UNIT           stop a program started with run
    odd1-agent-desktop show | hide [--wait S]
"""
import json
import sys

from . import desktop


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("run", "status", "stop", "show", "hide"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    try:
        if command == "run":
            result = desktop.run(rest)
        elif command == "stop":
            result = desktop.stop(rest[0])
        elif command == "hide":
            result = desktop.hide(float(rest[1]) if rest[:1] == ["--wait"] else 0)
        else:
            result = getattr(desktop, command)()
    except (OSError, ValueError, RuntimeError, IndexError) as exc:
        print(f"odd1-agent-desktop {command}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
