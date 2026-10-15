"""The caretaker daemon (systemd unit odd1-caretaker): look at the machine every ODD1_CARETAKER_INTERVAL seconds,
write the report files (see store.py), and restart failed units that are on ODD1_CARETAKER_RESTART.

A failed look is recorded in health.json (ok=false, the last success kept) and printed to the journal; the loop
keeps going. It never makes model calls.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from . import probe, repair, store


def restart_list() -> list[str]:
    return os.environ.get("ODD1_CARETAKER_RESTART", "").split()


def run_once(directory: Path | None = None, snapshot=probe.snapshot, run=subprocess.run, sleep=time.sleep) -> dict:
    directory = directory or store.state_dir()
    with store.locked(directory):
        try:
            snap = snapshot()
            actions = repair.auto_repair(snap, restart_list(), directory, run, sleep)
            if actions:
                snap["repairs"] = actions
            store.record_success(directory, snap)
            return snap
        except Exception as exc:
            store.record_error(directory, exc)
            raise


def main() -> None:
    interval = float(os.environ.get("ODD1_CARETAKER_INTERVAL", "60"))
    while True:
        try:
            snap = run_once()
            print(f"caretaker: level={snap.get('level')} problems={len(snap.get('problems') or [])}"
                  + "".join(f"\n  {p}" for p in snap.get("problems") or []), flush=True)
        except Exception as exc:  # visible in the journal and in health.json; keep watching
            print(f"caretaker: look failed: {type(exc).__name__}: {str(exc)[:500]}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
