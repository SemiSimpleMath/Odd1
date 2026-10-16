#!/usr/bin/env python3
"""Installed vs available versions, as JSON: Claude Code (npm) and the Python packages Odd1's components install
(PyPI). The package list comes from the Nix build ($ODD1_TASKS_PYPKGS). Reads only; never changes anything.

    python3 check-versions.py
"""
import email.parser
import glob
import json
import os
import re
import subprocess
import urllib.request


def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def pypi(name):
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=30) as r:
            return json.load(r)["info"]["version"]
    except Exception as e:  # not on PyPI, or unreachable: say so instead of guessing
        return f"unknown ({type(e).__name__})"


def dist(p):
    """(PyPI name, version) from the package's own .dist-info in the Nix store; None if it has none (not on PyPI)."""
    for meta in glob.glob(os.path.join(p.get("path", ""), "lib/python3*/site-packages/*.dist-info/METADATA")):
        m = email.parser.Parser().parse(open(meta), headersonly=True)
        return m["Name"], m["Version"]
    return None


def main():
    rows = [{"name": "@anthropic-ai/claude-code", "source": "npm",
             "installed": (re.findall(r"\d+\.\d+\.\d+", run("claude", "--version")) or ["unknown"])[0],
             "available": run("npm", "view", "@anthropic-ai/claude-code", "version") or "unknown"}]
    path = os.environ.get("ODD1_TASKS_PYPKGS", "/etc/odd1/tasks/python-packages.json")
    for p in json.load(open(path)) if os.path.exists(path) else []:
        found = dist(p)
        if found is None:  # e.g. tkinter (part of Python), pyatspi (not published on PyPI)
            rows.append({"name": p["name"], "source": "nix only", "installed": p["version"], "available": "unknown"})
        else:
            rows.append({"name": found[0], "source": "pypi (installed by Nix)", "installed": found[1],
                         "available": pypi(found[0])})
    for r in rows:
        r["update"] = r["installed"] != r["available"] and not r["available"].startswith("unknown")
    print(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
