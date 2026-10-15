"""Dump every application's top-level windows and their AT-SPI trees as one JSON document (run as a child process,
so a stuck accessibility bus cannot hang the screen service).

    python -m odd1_screen.atspi_dump [--max-elements N]

Reuses any-app's bounded tree walker; adds what the screen model needs to match windows: the process id.
"""
import argparse
import json
import sys

from any_app import describe_accessible as da

WINDOW_ROLES = {"frame", "window", "dialog", "alert", "file chooser"}


def dump(desktop, pyatspi, max_elements=1500, max_apps=200):
    apps = []
    children, truncated = da.children(desktop, limit=max_apps)
    for _, app in children:
        try:
            pid = int(app.get_process_id())
        except Exception:
            pid = None
        frames = []
        for index, window in da.children(app, limit=50)[0]:
            role = da.role_name(window)
            if role.casefold() not in WINDOW_ROLES:
                continue
            elements, tree_truncated, _ = da.describe_window(window, pyatspi, max_elements=max_elements)
            frames.append({"index": index, "title": da.node_name(window), "role": role, "elements": elements,
                           "truncated": tree_truncated})
        apps.append({"app": da.node_name(app), "pid": pid, "frames": frames})
    return {"apps": apps, "truncated": truncated}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-elements", type=int, default=1500)
    args = parser.parse_args(argv)
    import pyatspi
    result = dump(pyatspi.Registry.getDesktop(0), pyatspi, max_elements=args.max_elements)
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
