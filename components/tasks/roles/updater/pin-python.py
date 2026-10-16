#!/usr/bin/env python3
"""Pin a Python package of Odd1's components to a newer PyPI version, in the current worktree's
nix/python-updates.json (version, sdist url, and the SHA-256 PyPI publishes: nothing is downloaded), and print the
components whose Python has it: their tests must pass (odd1-tasks-check COMPONENT...).

    python3 pin-python.py NAME VERSION [--major]   NAME: the PyPI name (PyGObject) or the Nix attribute (pygobject3)

Exit 4, changing nothing, for a major version jump (it may break components: a person decides those); --major pins
it anyway, once a person has approved that jump.
"""
import base64
import json
import os
import re
import sys
import urllib.request
from pathlib import Path


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.lower()).removeprefix("python-")


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--major"]
    if len(args) != 2:
        print(__doc__)
        return 2
    name, version = args
    known = json.load(open(os.environ.get("ODD1_TASKS_PYPKGS", "/etc/odd1/tasks/python-packages.json")))
    entry = next((p for p in known if norm(name) in (norm(p["attr"]), norm(p["name"]))), None)
    if entry is None:
        print(f"{name} is not a Python package of Odd1's components: {', '.join(p['attr'] for p in known)}")
        return 2
    installed = entry["version"]
    if installed.split(".")[0] != version.split(".")[0] and "--major" not in sys.argv:
        print(f"{entry['attr']} {installed} -> {version} is a major update: escalate it (a person decides)")
        return 4
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as r:
        release = json.load(r)
    sdist = next((u for u in release["urls"] if u["packagetype"] == "sdist"), None)
    if sdist is None:
        print(f"{name} {version} has no source distribution on PyPI")
        return 2
    path = Path("nix/python-updates.json")
    pins = json.loads(path.read_text()) if path.exists() else {}
    pins[entry["attr"]] = {"version": version, "url": sdist["url"], "pypi": name,
                           "hash": "sha256-" + base64.b64encode(bytes.fromhex(sdist["digests"]["sha256"])).decode()}
    path.write_text(json.dumps(pins, indent=2, sort_keys=True) + "\n")
    users = sorted(m.parent.name for m in Path("components").glob("*/module.nix")
                   if re.search(rf"withPackages \(p: \[[^\]]*\bp\.{re.escape(entry['attr'])}\b", m.read_text()))
    print(json.dumps({"pinned": entry["attr"], "from": installed, "to": version, "components": users}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
