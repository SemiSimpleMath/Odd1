"""odd1-stats sample | summary [--days N] [--json] | trend [--days N] [--json]"""
import argparse
import json
import sys

from . import collect, report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="odd1-stats", description="Odd1's agent and system stats")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample", help="take one sample now (the timer does this every 15 minutes)")
    for name, what in (("summary", "totals over the last N days"),
                       ("trend", "the last N days against the N before; flags regressions")):
        p = sub.add_parser(name, help=what)
        p.add_argument("--days", type=float, default=7)
        p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    p = collect.paths()
    if a.cmd == "sample":
        s = collect.sample(p)
        print(f"sampled {s['time']}: {len(s['sessions'])} sessions changed")
        return 0
    if a.cmd == "summary":
        r = report.summary(p["state"], a.days)
        print(json.dumps(r, indent=1) if a.json else report.format_summary(r))
    else:
        r = report.trend(p["state"], a.days)
        print(json.dumps(r, indent=1) if a.json else report.format_trend(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
