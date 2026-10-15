"""The agent desktop: a second X display (Xephyr :1, with its own window manager, pointer and keyboard) shown as one
window on the person's screen, minimized unless they want to watch or take over.

Apps started here run as transient user units in odd1-agent.slice (lower CPU and IO weight than the person's
apps), with accessibility on, so the screen component can read and drive them.
"""
import os
import shlex
import shutil
import subprocess
import time

DISPLAY = os.environ.get("ODD1_AGENT_DISPLAY") or ":1"
USER_DISPLAY = os.environ.get("ODD1_USER_DISPLAY") or ":0"
TITLE = "Agent desktop"
SLICE = "odd1-agent.slice"
UNIT_PREFIX = "odd1-agent-app-"
APP_ENV = {"DISPLAY": DISPLAY, "GNOME_ACCESSIBILITY": "1", "QT_ACCESSIBILITY": "1"}


def _x(args: list[str], display: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, timeout=5, errors="replace",
                          env={**os.environ, "DISPLAY": display}).stdout


def parse_wmctrl(text: str) -> list[dict]:
    """`wmctrl -l -p` -> [{xid, desktop, pid, title}]."""
    found = []
    for line in text.splitlines():
        fields = line.split(None, 4)
        if len(fields) >= 4 and fields[0].startswith("0x"):
            found.append({"xid": fields[0], "pid": int(fields[2]) if fields[2].isdigit() else None,
                          "title": fields[4] if len(fields) == 5 else ""})
    return found


def windows() -> list[dict]:
    """Managed windows on the agent desktop."""
    return parse_wmctrl(_x(["wmctrl", "-l", "-p"], DISPLAY))


def running() -> bool:
    return subprocess.run(["xdpyinfo"], capture_output=True, timeout=5,
                          env={**os.environ, "DISPLAY": DISPLAY}).returncode == 0


def run_args(argv: list[str], unit: str) -> list[str]:
    """The systemd-run command that starts argv as a transient unit on the agent desktop."""
    program = shutil.which(argv[0]) or argv[0]
    # The caller's PATH too: the user manager's own is minimal, and apps start helpers by name.
    env = [f"--setenv={k}={v}" for k, v in {**APP_ENV, "PATH": os.environ.get("PATH", "")}.items()]
    return ["systemd-run", "--user", f"--slice={SLICE}", f"--unit={unit}", "--collect", "--quiet", *env,
            "--", program, *argv[1:]]


def run(command: str | list[str], wait_s: float = 10) -> dict:
    """Start a program on the agent desktop; wait up to wait_s for its first window. Returns {unit, windows}."""
    argv = shlex.split(command) if isinstance(command, str) else list(command)
    if not argv:
        raise ValueError("empty command")
    if shutil.which(argv[0]) is None:
        raise FileNotFoundError(f"no program {argv[0]!r} on PATH")
    if not running():
        raise RuntimeError(f"the agent desktop ({DISPLAY}) is not running: systemctl --user start odd1-agent-desktop")
    before = {w["xid"] for w in windows()}
    unit = f"{UNIT_PREFIX}{os.path.basename(argv[0])}-{time.strftime('%H%M%S')}-{os.getpid() % 1000}"
    subprocess.run(run_args(argv, unit), check=True, capture_output=True, timeout=10)
    deadline = time.monotonic() + wait_s
    new: list[dict] = []
    while time.monotonic() < deadline and not new:
        time.sleep(0.3)
        new = [w for w in windows() if w["xid"] not in before]
    return {"unit": f"{unit}.service", "display": DISPLAY, "windows": new}


def apps() -> list[dict]:
    """Programs agents started here that are still running: [{unit, state, pid}]."""
    out = subprocess.run(["systemctl", "--user", "list-units", f"{UNIT_PREFIX}*", "--plain", "--no-legend",
                          "--all"], capture_output=True, text=True, timeout=10).stdout
    result = []
    for line in out.splitlines():
        unit = line.split(None, 1)[0] if line.strip() else ""
        if unit.startswith(UNIT_PREFIX):
            props = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "MainPID"],
                                   capture_output=True, text=True, timeout=10).stdout
            values = dict(p.split("=", 1) for p in props.split() if "=" in p)
            result.append({"unit": unit, "state": values.get("ActiveState"), "pid": int(values.get("MainPID", 0))})
    return result


def stop(unit: str) -> dict:
    """Stop one program agents started here (only odd1-agent-app-* units)."""
    if not unit.startswith(UNIT_PREFIX):
        raise PermissionError(f"only {UNIT_PREFIX}* units can be stopped here")
    subprocess.run(["systemctl", "--user", "stop", unit], check=True, capture_output=True, timeout=30)
    return {"stopped": unit}


def host_window() -> str | None:
    """The agent desktop's window on the person's screen."""
    return next((w["xid"] for w in parse_wmctrl(_x(["wmctrl", "-l", "-p"], USER_DISPLAY)) if w["title"] == TITLE),
                None)


def show() -> dict:
    """Bring the agent desktop up on the person's screen (to watch, or take over with mouse and keyboard)."""
    xid = host_window()
    if xid is None:
        raise RuntimeError("the agent desktop window is not on the person's screen")
    _x(["wmctrl", "-i", "-a", xid], USER_DISPLAY)
    return {"shown": xid}


def hide(wait_s: float = 0) -> dict:
    """Minimize the agent desktop on the person's screen; apps there keep running and stay drivable."""
    deadline = time.monotonic() + wait_s
    while (xid := host_window()) is None and time.monotonic() < deadline:
        time.sleep(0.3)
    if xid is None:
        raise RuntimeError("the agent desktop window is not on the person's screen")
    # A window that has only just appeared can miss the request (the window manager is still taking it on):
    # repeat until it reports itself minimized.
    for _ in range(max(1, int(wait_s / 0.3))):
        _x(["xdotool", "windowminimize", xid], USER_DISPLAY)
        time.sleep(0.3)
        if "_NET_WM_STATE_HIDDEN" in _x(["xprop", "-id", xid, "_NET_WM_STATE"], USER_DISPLAY):
            return {"hidden": xid}
    return {"hidden": xid, "confirmed": False}


def status() -> dict:
    up = running()
    return {"display": DISPLAY, "running": up, "host_window": host_window(),
            "windows": windows() if up else [], "apps": apps()}
