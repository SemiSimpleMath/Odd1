"""odd1-caretaker: the machine's health and the foreground program's resource pressure, for people and scripts.

    odd1-caretaker status [--max-age 180]   is the caretaker current? what did it last see? (exit 0 healthy, 1 stale
                                            or unhealthy, 2 unknown)
    odd1-caretaker pressure                 PSI now, system and the foreground program's cgroup (JSON)
    odd1-caretaker look                     a full look now, printed, not recorded
    odd1-caretaker once                     a full look now, recorded in the state directory (what the daemon does)
    odd1-caretaker repair UNIT              restart a systemd unit and verify it came back (recorded)
    odd1-caretaker run [limits] -- CMD ...  run CMD under resource limits and measure it (see run.py)
    odd1-caretaker watch                    the daemon loop (systemd unit odd1-caretaker)

State directory: $ODD1_CARETAKER_STATE (default /var/lib/odd1/caretaker).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import observer, probe, repair, run, status, store


def _print(data) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="odd1-caretaker", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status")
    p.add_argument("--max-age", type=int, default=180)
    for name in ("pressure", "look", "once", "watch"):
        sub.add_parser(name)
    p = sub.add_parser("repair")
    p.add_argument("unit")
    p = sub.add_parser("run")
    for flag, default in (("memory-mib", 1024), ("cpu-percent", 200), ("tasks", 512), ("runtime-seconds", 120)):
        p.add_argument(f"--{flag}", type=int, default=default)
    p.add_argument("--system", action="store_true", help="a system unit instead of the user's (needs root)")
    p.add_argument("--report-json", help="also write the measurement here")
    p.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)

    if a.cmd == "status":
        s = status.status(max_age_seconds=a.max_age)
        _print(s)
        state = s["caretaker"]["state"]
        return 0 if state == "healthy" else 1 if state in ("stale", "unhealthy") else 2
    if a.cmd == "pressure":
        _print(probe.pressure())
    elif a.cmd == "look":
        _print(probe.snapshot())
    elif a.cmd == "once":
        _print(observer.run_once())
    elif a.cmd == "watch":
        observer.main()
    elif a.cmd == "repair":
        result = repair.restart(a.unit)
        _print(result)
        return 0 if result["ok"] else 1
    elif a.cmd == "run":
        command = a.command[1:] if a.command[:1] == ["--"] else a.command
        try:
            report = run.run_measured(command, run.Limits(a.memory_mib, a.cpu_percent, a.tasks, a.runtime_seconds),
                                      user=not a.system)
        except ValueError as exc:
            print(f"odd1-caretaker run: {exc}", file=sys.stderr)
            return 2
        if a.report_json:
            store.write_json(Path(a.report_json), report)
        code = report["command_exit"]
        return 3 if code == 0 and report["outcome"] == "telemetry-incomplete" else int(code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
