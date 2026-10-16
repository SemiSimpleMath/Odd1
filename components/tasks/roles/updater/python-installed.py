#!/usr/bin/env python3
"""Exit 0 when Odd1's own Python (the components' odd1Python, as the Nix build lists it in
/etc/odd1/tasks/python-packages.json) has the Nix attribute ATTR at VERSION; 1 otherwise. The done_when of a Python
update (check-versions.py writes it): the system's python3 does not have these packages, so importlib.metadata there
cannot tell.

    python3 python-installed.py ATTR VERSION
"""
import json
import os
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    attr, version = sys.argv[1:]
    known = json.load(open(os.environ.get("ODD1_TASKS_PYPKGS", "/etc/odd1/tasks/python-packages.json")))
    installed = next((p["version"] for p in known if p["attr"] == attr), None)
    print(f"{attr}: {installed or 'not a package of the components'} installed, {version} wanted")
    return 0 if installed == version else 1


if __name__ == "__main__":
    sys.exit(main())
