"""The health icon's drop-down: a small window under the top bar with health.summary(), refreshed every 5 s.

`odd1-caretaker popup` toggles it: a second click on the icon closes the open one (its pid is in the runtime
folder). Esc, or a click anywhere else (the window loses focus), closes it too. GTK 3, the desktop's night palette.
"""
from __future__ import annotations

import os
import signal
import time
from datetime import datetime
from pathlib import Path

from . import health

WIDTH = 400
PANEL_HEIGHT = 34  # the top bar (desktop/xfconf/xfce4-panel.xml)
CSS = b"""
window { background: #1C2134; border: 1px solid #2E3656; border-radius: 0 0 10px 10px; }
* { font-family: Inter; color: #E4E4E8; }
.title { font-size: 13pt; font-weight: 600; }
.section { font-size: 9pt; font-weight: 600; color: #8A8C96; letter-spacing: 1px; margin-top: 10px; }
.dim { color: #8A8C96; font-size: 9.5pt; }
.value { font-size: 10pt; }
.reason { font-size: 10pt; }
.ok { color: #6FD3A0; } .warn { color: #E9C27E; } .bad { color: #E8737C; }
progressbar trough { min-height: 6px; background: #2E3656; border-radius: 3px; border: none; }
progressbar progress { min-height: 6px; border-radius: 3px; border: none; background: #6FAEF0; }
progressbar.warn progress { background: #E9C27E; } progressbar.bad progress { background: #E8737C; }
"""


def pidfile() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "odd1-health-popup.pid"


def close_open() -> bool:
    """Close a popup that is already open (the icon's second click). True when there was one."""
    path = pidfile()
    try:
        pid = int(path.read_text())
        if Path(f"/proc/{pid}/cmdline").read_bytes().find(b"popup") < 0:
            return False
        os.kill(pid, signal.SIGTERM)
        path.unlink(missing_ok=True)
        return True
    except (OSError, ValueError):
        return False


def _short(text: str, limit: int = 64) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(" ,:;(") + "…"


def rows(s: dict, now: float | None = None) -> list[tuple]:
    """What the window shows, as (kind, ...) rows; pure, so it is tested without a display."""
    now = now or time.time()
    out = [("title", s.get("level", "warn"), health.WORDS.get(s.get("level"), "?"))]
    out += [("reason", s.get("level", "warn"), r) for r in s.get("reasons", [])[:5]]
    m = s.get("machine") or {}
    out.append(("section", "MACHINE"))
    if m.get("memory_used_pct") is not None:
        used = (m["memory_total"] or 0) - (m["memory_available"] or 0)
        out.append(("bar", "Memory", m["memory_used_pct"],
                    f"{m['memory_used_pct']:.0f}%  ({health._gb(used)} of {health._gb(m['memory_total'])})"))
    if m.get("disk_used_pct") is not None:
        out.append(("bar", "Disk", m["disk_used_pct"], f"{m['disk_used_pct']:.0f}%  ({health._gb(m['disk_free'])} free)"))
    if m.get("load") is not None:
        p = m.get("pressure") or {}
        stalls = ", ".join(f"{k} {v:.0f}%" for k, v in p.items() if v is not None)
        out.append(("line", "CPU", f"load {m['load']:.1f} on {m.get('cpus') or '?'} cores", f"waiting: {stalls}"))
    failed = m.get("failed_units") or []
    out.append(("line", "Services", f"{len(failed)} failed: {', '.join(failed)}" if failed else "all running", ""))
    a = s.get("agents") or {}
    out.append(("section", "AGENTS"))
    for w in a.get("working", []):
        minutes = f" ({w['minutes']} min)" if w.get("minutes") is not None else ""
        out.append(("line", w["role"], f"#{w['task']} {_short(w['goal'])}", f"working{minutes}"))
    if not a.get("working"):
        out.append(("line", "Roles", "all idle", ""))
    runs = a.get("runs_today") or {}
    out.append(("line", "Runs today", str(sum(runs.values())),
                ", ".join(f"{k} {v}" for k, v in sorted(runs.items(), key=lambda kv: -kv[1]))))
    out.append(("line", "Waiting", f"{a.get('need_person', 0)} for you, {a.get('with_manager', 0)} for the manager", ""))
    out.append(("line", "Chats", f"{a.get('chats', 0)} Claude Code open", ""))
    b, d = s.get("backups") or {}, s.get("deploy") or {}
    out.append(("section", "SAFETY NET"))
    out.append(("line", "Backups", f"hourly {health.ago(b.get('hourly'), now)}", f"daily {health.ago(b.get('daily'), now)}"))
    last = d.get("last") or {}
    if d.get("current"):
        when = health.ago(last.get("at"), now) if last else ""
        by = str(last.get("actor") or "").replace("person:agent", "you")
        out.append(("line", "Running", f"{d['current']}, deployed {when} by {by}", (last.get("reason") or "")[:70]))
        out.append(("line", "Rollbacks", f"{d.get('failed_24h', 0)} in 24 h", ""))
    out.append(("dim", f"Updated {datetime.fromtimestamp(now):%H:%M:%S} · Esc closes"))
    return out


def just_closed(seconds: float = 0.8) -> bool:
    """A click on the icon first takes the focus from an open popup (which closes on that): that click closed it."""
    try:
        return time.time() - pidfile().with_suffix(".closed").stat().st_mtime < seconds
    except OSError:
        return False


def main() -> int:
    if close_open() or just_closed():
        return 0
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    win = Gtk.Window(title="Odd1 health")
    win.set_decorated(False)
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_keep_above(True)
    win.set_type_hint(Gdk.WindowTypeHint.DROPDOWN_MENU)
    win.set_default_size(WIDTH, -1)
    win.set_resizable(False)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=16)
    win.add(box)

    def label(text, *classes, xalign=0.0, wrap=True):
        lab = Gtk.Label(label=text, xalign=xalign)
        lab.set_line_wrap(wrap)
        lab.set_max_width_chars(48)
        for c in classes:
            lab.get_style_context().add_class(c)
        return lab

    def fill():
        for child in box.get_children():
            box.remove(child)
        s = health.summary()
        for row in rows(s):
            kind = row[0]
            if kind == "title":
                line = Gtk.Box(spacing=8)
                line.pack_start(label("●", row[1], wrap=False), False, False, 0)
                line.pack_start(label(row[2], "title", wrap=False), False, False, 0)
                box.pack_start(line, False, False, 0)
            elif kind == "reason":
                box.pack_start(label(f"• {row[2]}", "reason", row[1]), False, False, 0)
            elif kind == "section":
                box.pack_start(label(row[1], "section"), False, False, 0)
            elif kind == "bar":
                line = Gtk.Box(spacing=10)
                line.pack_start(label(row[1], "dim", wrap=False), False, False, 0)
                line.pack_end(label(row[3], "value", wrap=False), False, False, 0)
                box.pack_start(line, False, False, 0)
                bar = Gtk.ProgressBar(fraction=max(0.0, min(1.0, row[2] / 100)))
                if row[2] >= 85:
                    bar.get_style_context().add_class("bad" if row[2] >= 92 else "warn")
                box.pack_start(bar, False, False, 2)
            elif kind == "line":
                grid = Gtk.Box(spacing=10)
                name = label(row[1], "dim", wrap=False)
                name.set_size_request(84, -1)
                name.set_valign(Gtk.Align.START)
                grid.pack_start(name, False, False, 0)
                right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                right.pack_start(label(row[2], "value"), False, False, 0)
                if row[3]:
                    right.pack_start(label(row[3], "dim"), False, False, 0)
                grid.pack_start(right, True, True, 0)
                box.pack_start(grid, False, False, 0)
            elif kind == "dim":
                lab = label(row[1], "dim")
                lab.set_margin_top(10)
                box.pack_start(lab, False, False, 0)
        box.show_all()
        win.resize(WIDTH, 1)
        return True

    def place():
        display = Gdk.Display.get_default()
        _, x, _ = display.get_default_seat().get_pointer().get_position()
        geo = display.get_monitor_at_point(x, 0).get_workarea()
        win.move(max(geo.x + 8, min(x - WIDTH // 2, geo.x + geo.width - WIDTH - 8)), PANEL_HEIGHT)

    def quit(*_):
        if not Gtk.main_level():
            return
        pidfile().unlink(missing_ok=True)
        pidfile().with_suffix(".closed").touch()
        Gtk.main_quit()

    fill()
    place()
    win.connect("key-press-event", lambda w, e: quit() if e.keyval == Gdk.KEY_Escape else None)
    win.connect("focus-out-event", lambda *a: quit() and False)
    win.connect("destroy", quit)
    GLib.timeout_add_seconds(5, fill)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: quit() or False)
    pidfile().write_text(str(os.getpid()))
    win.show_all()
    win.present()
    Gtk.main()
    return 0
