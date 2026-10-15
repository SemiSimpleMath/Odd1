"""One look at the machine: pressure (PSI), memory, load, disks, failed systemd units, and the foreground program.

Everything here only reads (/proc, /sys/fs/cgroup, `systemctl list-units`, `xdotool`). Parsers are pure functions
so they can be tested anywhere; `snapshot()` puts them together on a live Linux machine.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

PROC = Path("/proc")
CGROUP = Path("/sys/fs/cgroup")
# PSI "some avg10" (percent of the last 10 s in which at least one task stalled on the resource).
ELEVATED, HIGH = 10.0, 40.0
LEVELS = ("ok", "elevated", "high")
DISK_PATHS = ("/", "/var/lib", "/nix/store")


def parse_psi(text: str) -> dict:
    """'some avg10=1.00 avg60=0.50 avg300=0.10 total=123' lines -> {"some": {"avg10": 1.0, ...}, "full": {...}}."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] not in ("some", "full"):
            continue
        fields = {}
        for item in parts[1:]:
            key, _, value = item.partition("=")
            try:
                fields[key] = int(value) if key == "total" else float(value)
            except ValueError:
                continue
        out[parts[0]] = fields
    return out


def psi_level(psi: dict | None) -> str:
    """ok / elevated / high from a parsed PSI file. Any stall of every task ('full') counts as high sooner."""
    if not psi:
        return "ok"
    some = psi.get("some", {}).get("avg10", 0.0)
    full = psi.get("full", {}).get("avg10", 0.0)
    if some >= HIGH or full >= ELEVATED:
        return "high"
    if some >= ELEVATED:
        return "elevated"
    return "ok"


def worst(levels) -> str:
    return max(levels, key=LEVELS.index, default="ok")


def parse_meminfo(text: str) -> dict:
    """/proc/meminfo -> {"MemTotal": bytes, ...}."""
    out = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            out[key.strip()] = int(parts[0]) * (1024 if parts[1:2] == ["kB"] else 1)
    return out


def parse_cgroup(text: str) -> str | None:
    """/proc/PID/cgroup -> the cgroup v2 path ('0::/user.slice/...'), or None on a v1-only system."""
    for line in text.splitlines():
        if line.startswith("0::"):
            return line[3:].strip() or "/"
    return None


def parse_failed_units(text: str) -> list[str]:
    """`systemctl list-units --failed --plain --no-legend` -> unit names."""
    units = []
    for line in text.splitlines():
        parts = [p for p in line.split() if p not in ("●", "*")]
        if parts and "." in parts[0]:
            units.append(parts[0])
    return units


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _int(text: str | None) -> int | None:
    text = (text or "").strip()
    return int(text) if text.isdigit() else None


def cgroup_state(path: str, root: Path = CGROUP) -> dict:
    """Memory use and pressure of one cgroup v2 directory."""
    d = root / path.lstrip("/")
    psi = {name: parse_psi(_read(d / f"{name}.pressure") or "") or None for name in ("cpu", "memory", "io")}
    limit = (_read(d / "memory.max") or "").strip()
    return {"cgroup": path, "memory_current": _int(_read(d / "memory.current")),
            "memory_max": None if limit in ("", "max") else _int(limit),
            "pressure": psi, "level": worst(psi_level(p) for p in psi.values())}


def process(pid: int, proc: Path = PROC, cgroups: Path = CGROUP) -> dict:
    """Name, resident memory and cgroup pressure of one process."""
    d = proc / str(pid)
    status = parse_meminfo(_read(d / "status") or "")  # same "Key: N kB" shape as /proc/meminfo
    cmdline = (_read(d / "cmdline") or "").replace("\0", " ").strip()
    out = {"pid": pid, "name": (_read(d / "comm") or "").strip() or None, "cmdline": cmdline[:300] or None,
           "rss": status.get("VmRSS")}
    cg = parse_cgroup(_read(d / "cgroup") or "")
    if cg:
        out.update(cgroup_state(cg, cgroups))
    return out


def foreground_pid(run=subprocess.run, display: str | None = None) -> tuple[int | None, str | None]:
    """PID of the program owning the focused window on the graphical session, via xdotool."""
    env = dict(os.environ, DISPLAY=display or os.environ.get("DISPLAY") or ":0")
    try:
        r = run(["xdotool", "getactivewindow", "getwindowpid"], capture_output=True, text=True, timeout=5,
                env=env, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"xdotool unavailable: {type(exc).__name__}"
    pid = (r.stdout or "").strip()
    if r.returncode != 0 or not pid.isdigit():
        return None, "no focused window with a known PID" + (f": {r.stderr.strip()[:200]}" if r.stderr else "")
    return int(pid), None


def failed_units(run=subprocess.run) -> tuple[list[str] | None, str | None]:
    try:
        r = run(["systemctl", "list-units", "--failed", "--plain", "--no-legend", "--no-pager"],
                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"systemctl unavailable: {type(exc).__name__}"
    if r.returncode != 0:
        return None, f"systemctl exit {r.returncode}: {(r.stderr or '').strip()[:200]}"
    return parse_failed_units(r.stdout), None


def disks(paths=DISK_PATHS) -> dict:
    out = {}
    for p in paths:
        try:
            u = shutil.disk_usage(p)
        except OSError:
            continue
        out[p] = {"total": u.total, "free": u.free, "used_pct": round(100 * u.used / u.total, 1) if u.total else None}
    return out


def problems(snap: dict) -> list[str]:
    """Plain statements of what needs attention, for people and agents alike."""
    out = []
    if not any((snap.get("pressure") or {}).values()):
        out.append("cannot see pressure: no PSI in /proc/pressure (kernel without CONFIG_PSI, or not Linux)")
    if snap.get("failed_units_unavailable"):
        out.append(f"cannot see failed units: {snap['failed_units_unavailable']}")
    for name, psi in (snap.get("pressure") or {}).items():
        lvl = psi_level(psi)
        if lvl != "ok":
            out.append(f"{name} pressure {lvl}: some avg10={psi.get('some', {}).get('avg10')}%")
    mem = snap.get("memory") or {}
    if mem.get("MemTotal") and mem.get("MemAvailable") is not None and mem["MemAvailable"] < 0.1 * mem["MemTotal"]:
        out.append(f"memory low: {mem['MemAvailable'] // 2**20} MiB available of {mem['MemTotal'] // 2**20} MiB")
    for path, d in (snap.get("disks") or {}).items():
        if d.get("used_pct") is not None and d["used_pct"] >= 90:
            out.append(f"disk {path} {d['used_pct']}% full")
    for unit in snap.get("failed_units") or []:
        out.append(f"systemd unit failed: {unit}")
    fg = snap.get("foreground") or {}
    if fg.get("level") in ("elevated", "high"):
        out.append(f"foreground {fg.get('name')} (pid {fg.get('pid')}) under {fg['level']} pressure in {fg.get('cgroup')}")
    return out


def pressure(proc: Path = PROC, run=subprocess.run, cgroups: Path = CGROUP) -> dict:
    """The cheap part: system PSI plus the foreground program's cgroup pressure."""
    psi = {name: parse_psi(_read(proc / "pressure" / name) or "") or None for name in ("cpu", "memory", "io")}
    pid, why = foreground_pid(run)
    fg = process(pid, proc, cgroups) if pid else {"unavailable": why}
    return {"pressure": psi, "foreground": fg,
            "level": worst([*(psi_level(p) for p in psi.values()), fg.get("level", "ok")])}


def snapshot(proc: Path = PROC, run=subprocess.run, cgroups: Path = CGROUP) -> dict:
    """Everything the caretaker watches, in one JSON-ready dict."""
    snap = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **pressure(proc, run, cgroups)}
    load = (_read(proc / "loadavg") or "").split()
    snap["load"] = [float(x) for x in load[:3]] if len(load) >= 3 else None
    snap["cpus"] = os.cpu_count()
    mem = parse_meminfo(_read(proc / "meminfo") or "")
    snap["memory"] = {k: mem.get(k) for k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")} if mem else None
    snap["disks"] = disks()
    snap["failed_units"], why = failed_units(run)
    if why:
        snap["failed_units_unavailable"] = why
    snap["problems"] = problems(snap)
    return snap
