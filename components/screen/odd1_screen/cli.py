"""odd1-screen: the screen model from the command line (prints JSON).

    odd1-screen view [WINDOW] [--ocr auto|always|never] [--hidden]
    odd1-screen at X Y
    odd1-screen watch [WINDOW] [--interval S]     print changes as they happen (one JSON line each)
"""
import argparse
import json
import time

from . import live, model


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="odd1-screen", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    view = sub.add_parser("view")
    view.add_argument("window", nargs="?", default="")
    view.add_argument("--ocr", default="auto", choices=("auto", "always", "never"))
    view.add_argument("--hidden", action="store_true")
    at = sub.add_parser("at")
    at.add_argument("x", type=int)
    at.add_argument("y", type=int)
    watch = sub.add_parser("watch")
    watch.add_argument("window", nargs="?", default="")
    watch.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args(argv)
    screen = model.Screen()
    if args.command == "view":
        print(json.dumps(live.observe(screen, window=args.window, ocr=args.ocr, include_hidden=args.hidden),
                         ensure_ascii=False))
    elif args.command == "at":
        live.observe(screen, ocr="never")
        print(json.dumps(screen.at(args.x, args.y), ensure_ascii=False))
    else:
        before = live.observe(screen, window=args.window)
        while True:
            time.sleep(args.interval)
            after = live.observe(screen, window=args.window, ocr="never")
            diff = model.changes(before, after)
            if any(diff.values()):
                print(json.dumps(diff, ensure_ascii=False), flush=True)
            before = after
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
