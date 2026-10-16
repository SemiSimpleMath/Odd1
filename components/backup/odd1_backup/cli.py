"""odd1-backup: local backups of Odd1's state (daily) and the person's home folder (hourly); restores never destroy.

    odd1-backup run [state|home]               take a snapshot now (both by default), then drop expired ones
    odd1-backup list                           the snapshots there are
    odd1-backup restore DATE [--to DIR]        put a state snapshot back (default /var/lib/odd1: stop its services)
    odd1-backup restore-file PATH [--at TIME]  put back a file or folder of the home folder as it was at TIME
                                               (e.g. 2026-10-16T09:00; default: the latest snapshot)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import backup


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="odd1-backup", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("what", nargs="?", choices=["state", "home", "all"], default="all")
    sub.add_parser("list")
    r = sub.add_parser("restore")
    r.add_argument("date")
    r.add_argument("--to", type=Path, default=backup.STATE)
    f = sub.add_parser("restore-file")
    f.add_argument("path", type=Path)
    f.add_argument("--at", type=datetime.fromisoformat)
    a = p.parse_args(argv)
    try:
        if a.cmd == "run":
            if a.what in ("state", "all"):
                print(f"state -> {backup.snapshot_state()}")
            if a.what in ("home", "all"):
                print(f"home -> {backup.snapshot_home()}")
            for gone in backup.prune():
                print(f"expired: {gone}")
        elif a.cmd == "list":
            for s in backup.state_snapshots():
                print(f"state {s.name}  {s}")
            for s in backup.home_snapshots():
                print(f"home  {s.name}  {s}")
        elif a.cmd == "restore":
            print(json.dumps(backup.restore_state(a.date, a.to), indent=1))
        else:
            print(json.dumps(backup.restore_file(a.path, a.at), indent=1))
    except backup.BackupError as e:
        print(f"odd1-backup: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
