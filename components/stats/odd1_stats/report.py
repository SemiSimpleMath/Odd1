"""Read the samples back: totals over a window (summary) and the last N days against the N before (trend)."""
import json
import time
from pathlib import Path

from .collect import DAY, day_of

ROLE_KEYS = ("runs", "done", "retry", "parked", "blocked", "budget_hits", "done_after_retry", "turns", "cost_usd",
             "minutes")
SESSION_KEYS = ("turns", "tokens_in", "tokens_out", "cache_read", "cache_write", "tool_calls", "tool_errors",
                "denials", "interrupts")
REGRESSION = 0.2  # a metric 20% worse than before is flagged
MIN_RUNS, MIN_CALLS = 3, 10  # below this, no verdict


def load(state: Path, start: float, end: float) -> list:
    out = []
    for f in sorted(state.glob("samples*.jsonl")):
        try:
            lines = f.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                s = json.loads(line)
            except ValueError:
                continue
            if start - DAY <= s.get("t", 0) < end + DAY:  # day stats may come from a sample around the edge
                out.append(s)
    return sorted(out, key=lambda s: s["t"])


def _add(into: dict, d: dict, keys) -> None:
    for k in keys:
        if d.get(k):
            into[k] = round(into.get(k, 0) + d[k], 4)


def aggregate(samples: list, start: float, end: float) -> dict:
    days = {day_of(t) for t in range(int(start - start % DAY), int(end), DAY)} if end > start else set()
    days = {d for d in days if day_of(start) <= d <= day_of(end - 1)}
    role_days, friction_days = {}, {}
    for s in samples:  # per day, the latest sample that has the day wins (it saw the whole day so far)
        t = s.get("tasks") or {}
        for day, roles in (t.get("roles") or {}).items():
            role_days[day] = roles
        for day, comps in (t.get("friction") or {}).items():
            friction_days[day] = comps
    roles, friction = {}, {}
    for day in days:
        for role, d in (role_days.get(day) or {}).items():
            _add(roles.setdefault(role, {}), d, ROLE_KEYS)
        for comp, d in (friction_days.get(day) or {}).items():
            _add(friction.setdefault(comp, {}), d, ("opened", "closed"))
    usage, tools, servers, errors_by_tool = {}, {}, {}, {}
    sessions = {}
    system = {"samples": 0, "psi_avg60_max": {}, "mem_avail_pct_min": None, "disk_used_pct_max": None,
              "failed_units_max": 0}
    deploys = {"switches": 0, "failed": 0, "rollbacks": 0}
    for s in samples:
        if not start <= s.get("t", 0) < end:
            continue
        for sid, c in (s.get("sessions") or {}).items():
            src = c.get("source", "chat")
            _add(usage.setdefault(src, {}), c, SESSION_KEYS)
            sessions.setdefault(src, set()).add(sid)
            for name, n in (c.get("calls_by_tool") or {}).items():
                tools[name] = tools.get(name, 0) + n
            for name, n in (c.get("errors_by_tool") or {}).items():
                errors_by_tool[name] = errors_by_tool.get(name, 0) + n
        sysd = s.get("system") or {}
        if sysd:
            system["samples"] += 1
            for k, v in (sysd.get("psi_avg60") or {}).items():
                if v is not None:
                    system["psi_avg60_max"][k] = max(v, system["psi_avg60_max"].get(k, 0))
            if sysd.get("mem_avail_pct") is not None:
                m = system["mem_avail_pct_min"]
                system["mem_avail_pct_min"] = sysd["mem_avail_pct"] if m is None else min(m, sysd["mem_avail_pct"])
            if sysd.get("disk_used_pct_max") is not None:
                system["disk_used_pct_max"] = max(system["disk_used_pct_max"] or 0, sysd["disk_used_pct_max"])
            system["failed_units_max"] = max(system["failed_units_max"], sysd.get("failed_units") or 0)
        for d in s.get("deploys") or []:
            if d.get("action") == "switch":
                deploys["switches"] += 1
                deploys["failed"] += not d.get("ok")
                deploys["rollbacks"] += bool(d.get("rollback"))
    for src, ids in sessions.items():
        usage[src]["sessions"] = len(ids)
    for name in set(tools) | set(errors_by_tool):
        server = name.split("__")[1] if name.startswith("mcp__") and name.count("__") >= 2 else "builtin"
        v = servers.setdefault(server, {"calls": 0, "errors": 0})
        v["calls"] += tools.get(name, 0)
        v["errors"] += errors_by_tool.get(name, 0)
    by_tool = {name: {"calls": tools.get(name, 0), "errors": errors_by_tool.get(name, 0)}
               for name in set(tools) | set(errors_by_tool)}
    return {"from": day_of(start), "to": day_of(end - 1), "samples": sum(start <= s["t"] < end for s in samples),
            "roles": roles, "usage": usage, "tools": by_tool, "servers": servers, "friction": friction,
            "system": system, "deploys": deploys}


def summary(state: Path, days: float, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    start = now - days * DAY
    return aggregate(load(state, start, now), start, now)


# -- trend

def _ratio(a, b):
    return round(a / b, 4) if b else None


def metrics(agg: dict) -> dict:
    """Comparable rates. Each: (value, enough data?, higher is worse?)."""
    tot = {}
    for d in agg["roles"].values():
        _add(tot, d, ROLE_KEYS)
    out = {}
    done, ended = tot.get("done", 0), tot.get("done", 0) + tot.get("retry", 0) + tot.get("parked", 0)
    enough = ended >= MIN_RUNS
    out["success rate"] = (_ratio(done, ended), enough, False)
    out["turns per done task"] = (_ratio(tot.get("turns", 0), done), done >= MIN_RUNS, True)
    out["cost per done task"] = (_ratio(tot.get("cost_usd", 0), done), done >= MIN_RUNS, True)
    out["minutes per done task"] = (_ratio(tot.get("minutes", 0), done), done >= MIN_RUNS, True)
    out["budget hits per run"] = (_ratio(tot.get("budget_hits", 0), tot.get("runs", 0)), tot.get("runs", 0) >= MIN_RUNS, True)
    calls = sum(u.get("tool_calls", 0) for u in agg["usage"].values())
    errs = sum(u.get("tool_errors", 0) for u in agg["usage"].values())
    out["tool error rate"] = (_ratio(errs, calls), calls >= MIN_CALLS, True)
    sess = sum(u.get("sessions", 0) for u in agg["usage"].values())
    out["denials per session"] = (_ratio(sum(u.get("denials", 0) for u in agg["usage"].values()), sess), sess >= MIN_RUNS, True)
    out["interrupts per session"] = (_ratio(sum(u.get("interrupts", 0) for u in agg["usage"].values()), sess), sess >= MIN_RUNS, True)
    for name, v in agg["tools"].items():
        out[f"error rate {name}"] = (_ratio(v["errors"], v["calls"]), v["calls"] >= MIN_CALLS, True)
    opened = sum(v.get("opened", 0) for v in agg["friction"].values())
    out["friction tickets opened"] = (opened, opened > 0, True)  # none at all: no tickets yet, not no friction
    return out


def trend(state: Path, days: float, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    mid, start = now - days * DAY, now - 2 * days * DAY
    samples = load(state, start, now)
    recent, before = metrics(aggregate(samples, mid, now)), metrics(aggregate(samples, start, mid))
    rows = []
    for name in sorted(set(recent) | set(before), key=lambda n: (n.startswith("error rate "), n)):
        (a, ok_a, worse_up) = recent.get(name, (None, False, True))
        (b, ok_b, _) = before.get(name, (None, False, True))
        verdict = "n/a"
        if a is not None and b is not None and ok_a and ok_b:
            change = (a - b) / b if b else (1.0 if a > b else 0.0)
            worse = change > REGRESSION if worse_up else change < -REGRESSION
            better = change < -REGRESSION if worse_up else change > REGRESSION
            verdict = "WORSE" if worse else "better" if better else "same"
        if name.startswith("error rate ") and verdict == "n/a":
            continue
        rows.append({"metric": name, "recent": a, "before": b, "verdict": verdict})
    return {"days": days, "recent": f"{day_of(mid)}..{day_of(now - 1)}", "before": f"{day_of(start)}..{day_of(mid - 1)}",
            "regressions": [r["metric"] for r in rows if r["verdict"] == "WORSE"], "rows": rows}


# -- text

def _table(header: list, rows: list) -> str:
    cells = [header] + [[("" if c is None else f"{c:g}" if isinstance(c, float) else str(c)) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(header))]
    return "\n".join("  ".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(r, widths)))
                     for r in cells)


def format_summary(s: dict) -> str:
    out = [f"Odd1 stats {s['from']}..{s['to']} ({s['samples']} samples)", "", "Tasks by role"]
    rows = []
    for role, d in sorted(s["roles"].items()):
        done = d.get("done", 0)
        rows.append([role, d.get("runs", 0), done, d.get("retry", 0), d.get("parked", 0), d.get("blocked", 0),
                     d.get("budget_hits", 0), d.get("turns", 0), round(d.get("cost_usd", 0), 2),
                     round(d.get("minutes", 0), 1), round(d["turns"] / done, 1) if done and d.get("turns") else None,
                     round(d["cost_usd"] / done, 2) if done and d.get("cost_usd") else None])
    out.append(_table(["role", "runs", "done", "retry", "parked", "blocked", "budget", "turns", "cost$", "minutes",
                       "turns/done", "cost/done"], rows))
    out += ["", "Claude Code usage (routine = task runs, chat = the person's conversations)"]
    rows = [[src, u.get("sessions", 0), u.get("turns", 0), u.get("tokens_in", 0), u.get("tokens_out", 0),
             u.get("cache_read", 0), u.get("tool_calls", 0), u.get("tool_errors", 0), u.get("denials", 0),
             u.get("interrupts", 0)] for src, u in sorted(s["usage"].items())]
    out.append(_table(["source", "sessions", "turns", "tokens_in", "tokens_out", "cache_read", "tool_calls",
                       "tool_errors", "denials", "interrupts"], rows))
    out += ["", "Where the friction is: tool errors by tool (top 10)"]
    top = sorted(s["tools"].items(), key=lambda kv: -kv[1]["errors"])[:10]
    out.append(_table(["tool", "calls", "errors", "error%"], [[n, v["calls"], v["errors"],
                       round(100 * v["errors"] / v["calls"], 1) if v["calls"] else None] for n, v in top if v["errors"]]))
    out += ["", "Tool errors by MCP server"]
    out.append(_table(["server", "calls", "errors"], [[n, v["calls"], v["errors"]]
                                                     for n, v in sorted(s["servers"].items(), key=lambda kv: -kv[1]["errors"])]))
    out += ["", "Friction tickets by component"]
    out.append(_table(["component", "opened", "closed"], [[c, v.get("opened", 0), v.get("closed", 0)]
                                                          for c, v in sorted(s["friction"].items())]))
    sy, de = s["system"], s["deploys"]
    psi = ", ".join(f"{k} {v:g}%" for k, v in sorted(sy["psi_avg60_max"].items())) or "?"
    out += ["", f"System: PSI avg60 max {psi}; memory available min {sy['mem_avail_pct_min']}%; fullest disk "
                f"{sy['disk_used_pct_max']}%; failed units max {sy['failed_units_max']}",
            f"Deploys: {de['switches']} switches, {de['failed']} failed, {de['rollbacks']} rollbacks"]
    return "\n".join(out)


def format_trend(t: dict) -> str:
    out = [f"Odd1 trend: last {t['days']:g} days ({t['recent']}) against the {t['days']:g} before ({t['before']})"]
    out.append(_table(["metric", "recent", "before", "verdict"],
                      [[r["metric"], r["recent"], r["before"], r["verdict"]] for r in t["rows"]]))
    out.append("Regressions: " + (", ".join(t["regressions"]) or "none"))
    return "\n".join(out)
