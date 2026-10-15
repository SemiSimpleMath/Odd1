"""Run a command in its own transient systemd unit with resource limits, and measure what it used.

    odd1-caretaker run [--memory-mib 1024] [--cpu-percent 200] [--tasks 512] [--runtime-seconds 120] -- CMD ...

The command's stdout and stderr pass through unchanged; the measurement (peak memory, most tasks seen, systemd's
result, the command's exit and a one-word outcome) goes to stderr as one JSON line and to --report-json. This is how
an agent sizes a program before living with it (merged from the browser project's resource observer). It measures;
it is not a security boundary.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass

from . import store

PASS_ENV = ("PATH", "PYTHONPATH", "HOME", "DISPLAY", "XAUTHORITY", "MOZ_HEADLESS", "LANG")


@dataclass(frozen=True)
class Limits:
    memory_mib: int = 1024
    cpu_percent: int = 200
    tasks: int = 512
    runtime_seconds: int = 120

    def validate(self) -> None:
        for name, low, high in (("memory_mib", 32, 65536), ("cpu_percent", 1, 6400),
                                ("tasks", 1, 4096), ("runtime_seconds", 1, 86400)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name.replace('_', '-')} must be an integer in {low}..{high}")

    def properties(self) -> list[str]:
        return [f"MemoryMax={self.memory_mib}M", "MemorySwapMax=0", f"CPUQuota={self.cpu_percent}%",
                f"TasksMax={self.tasks}", f"RuntimeMaxSec={self.runtime_seconds}s"]


def outcome(systemd_result: str, command_exit: int | None, complete: bool) -> str:
    """One word on how it ended. The kernel's OOM kill and the runtime limit are named, not lumped into 'failed'."""
    if systemd_result == "oom-kill":
        return "resource-oom"
    if systemd_result in ("timeout", "watchdog"):
        return "runtime-limit"
    if not complete:
        return "telemetry-incomplete"
    if systemd_result == "success":
        return "success" if command_exit == 0 else "command-failed"
    return "systemd-failed"


def unit_metrics(unit: str, run=subprocess.run, user: bool = True) -> dict | None:
    scope = ["--user"] if user else []
    try:
        r = run(["systemctl", *scope, "show", unit, "--property=ActiveState,MemoryPeak,TasksCurrent,Result"],
                capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    found = dict(line.split("=", 1) for line in (r.stdout or "").splitlines() if "=" in line)
    if r.returncode or not {"ActiveState", "MemoryPeak", "TasksCurrent", "Result"} <= set(found):
        return None
    num = lambda v: int(v) if v.isdigit() else 0  # systemd prints [not set] when it does not know
    return {"active": found["ActiveState"], "memory_peak": num(found["MemoryPeak"]),
            "tasks": num(found["TasksCurrent"]), "result": found["Result"]}


def run_measured(command: list[str], limits: Limits = Limits(), user: bool = True, poll_ms: int = 100,
                 popen=subprocess.Popen, run=subprocess.run, sleep=time.sleep, out=sys.stdout, err=sys.stderr) -> dict:
    limits.validate()
    if not command:
        raise ValueError("a command is required after --")
    unit = f"odd1-caretaker-run-{uuid.uuid4().hex[:16]}"
    argv = ["systemd-run", *(["--user"] if user else []), "--pipe", "--wait", f"--unit={unit}",
            f"--property=WorkingDirectory={os.getcwd()}", *(f"--property={p}" for p in limits.properties())]
    argv += [f"--setenv={k}={os.environ[k]}" for k in PASS_ENV if k in os.environ]
    proc = popen([*argv, "--", *command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    peak = tasks = samples = 0
    result = ""

    def sample():
        nonlocal peak, tasks, samples, result
        m = unit_metrics(unit + ".service", run, user)
        if m:
            samples += m["active"] == "active"
            peak, tasks, result = max(peak, m["memory_peak"]), max(tasks, m["tasks"]), m["result"]
        return m

    while True:
        sample()
        try:
            stdout, stderr = proc.communicate(timeout=poll_ms / 1000)
            break
        except subprocess.TimeoutExpired:
            continue
    # systemd may still be settling the unit when systemd-run returns: read its terminal result once it has.
    for _ in range(10):
        m = sample()
        if m is None or m["active"] not in ("active", "activating", "deactivating"):
            break
        sleep(0.1)
    if result not in ("", "success"):  # a failed transient unit stays listed as failed until reset
        run(["systemctl", *(["--user"] if user else []), "reset-failed", unit + ".service"],
            capture_output=True, text=True, timeout=5, check=False)
    out.write(stdout or "")
    err.write(stderr or "")
    complete = samples > 0 and peak > 0 and tasks > 0 and result != ""
    report = {"unit": unit + ".service", "limits": limits.__dict__, "memory_peak_bytes": peak,
              "tasks_high": tasks, "active_samples": samples, "systemd_result": result,
              "command_exit": proc.returncode, "outcome": outcome(result, proc.returncode, complete),
              "time": store.now()}
    err.write("odd1-caretaker run: " + json.dumps(report, sort_keys=True) + "\n")
    return report
