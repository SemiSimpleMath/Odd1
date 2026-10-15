"""The X server's view of the desktop: managed windows in stacking order with their real client geometry.

X11 is the authority on where a window is and what is on top; toolkits (AT-SPI) often get their own window origin
wrong. Reads with xprop and xwininfo; the parsers are pure so they test without a display.
"""
import os
import re
import subprocess

TIMEOUT = 5


def env(display: str | None) -> dict:
    """The environment for an X client on `display` (default: this process's own)."""
    return {**os.environ, "DISPLAY": display} if display else dict(os.environ)


def _run(args: list[str], display: str | None = None) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=TIMEOUT,
                          errors="replace", env=env(display)).stdout


def parse_window_list(text: str) -> list[str]:
    """`xprop -root -notype _NET_CLIENT_LIST_STACKING` -> window ids, bottom first."""
    if "#" not in text:
        return []
    return [w.strip() for w in text.split("#", 1)[1].split(",") if w.strip().startswith("0x")]


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return re.sub(r"\\(.)", r"\1", value)


def parse_properties(text: str) -> dict:
    """`xprop -id W -notype _NET_WM_PID _NET_WM_NAME WM_NAME _NET_WM_STATE` -> {pid, title, states}."""
    props = {}
    for line in text.splitlines():
        if " = " in line:
            key, value = line.split(" = ", 1)
            props[key.strip()] = value
    pid = props.get("_NET_WM_PID", "").strip()
    title = props.get("_NET_WM_NAME") or props.get("WM_NAME") or '""'
    states = [s.strip().removeprefix("_NET_WM_STATE_").lower()
              for s in props.get("_NET_WM_STATE", "").split(",") if s.strip()]
    return {"pid": int(pid) if pid.isdigit() else None, "title": _unquote(title), "states": states}


def parse_geometry(text: str) -> dict | None:
    """`xwininfo -id W` -> {x, y, width, height, viewable}: absolute client-area geometry."""
    fields = {}
    for key, name in (("Absolute upper-left X", "x"), ("Absolute upper-left Y", "y"),
                      ("Width", "width"), ("Height", "height")):
        match = re.search(rf"^\s*{key}:\s*(-?\d+)", text, re.M)
        if not match:
            return None
        fields[name] = int(match.group(1))
    fields["viewable"] = "IsViewable" in text
    return fields


def parse_active(text: str) -> str | None:
    ids = parse_window_list(text)
    return ids[0] if ids else None


def windows(display: str | None = None) -> dict:
    """{"screen": {width, height}, "windows": [...] bottom to top, "active": xid}. Unreadable windows are skipped
    (a window can close between the list and its properties)."""
    root = parse_geometry(_run(["xwininfo", "-root"], display)) or {}
    stacking = _run(["xprop", "-root", "-notype", "_NET_CLIENT_LIST_STACKING", "_NET_ACTIVE_WINDOW"], display)
    lines = stacking.splitlines()
    ids = parse_window_list(next((l for l in lines if l.startswith("_NET_CLIENT_LIST_STACKING")), ""))
    active = parse_active(next((l for l in lines if l.startswith("_NET_ACTIVE_WINDOW")), ""))
    result = []
    for xid in ids:
        try:
            geometry = parse_geometry(_run(["xwininfo", "-id", xid], display))
            props = parse_properties(_run(["xprop", "-id", xid, "-notype", "_NET_WM_PID", "_NET_WM_NAME",
                                           "WM_NAME", "_NET_WM_STATE"], display))
        except (subprocess.SubprocessError, OSError):
            continue
        if geometry is None:
            continue
        result.append({"xid": xid, **props, "geometry": geometry})
    return {"screen": {"width": root.get("width", 0), "height": root.get("height", 0)},
            "windows": result, "active": active}
