"""Odd1 fitness check: keeps the repository in one shape as it grows. Run before every commit.

    python tools/check.py          check; exit 1 on any problem
    python tools/check.py --map    print the component map (name: first README line)

Plain string checks only. A problem is a prompt to split, merge or delete, not to weaken this file.
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENTS = ROOT / "components"
MAX_SOURCE_LINES = 3_000
MAX_FILE_BYTES = 1_000_000
MAX_NOTE_BYTES = 20_000
SOURCE_SUFFIXES = {".py", ".sh", ".nix", ".js", ".ts", ".c", ".h", ".rs", ".go"}
NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")
NEVER = ("__pycache__", ".venv", "node_modules", ".pytest_cache")
NEVER_SUFFIXES = (".log", ".pyc", ".qcow2", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4")
NOTE_NAMES = ("CONVERSATION", "TRANSCRIPT", "PROGRESS", "SESSION")
# Only components/deploy activates a system; anywhere else a direct nixos-rebuild could ship uncommitted work.
DIRECT_DEPLOY = re.compile(r"nixos-rebuild +(switch|boot|test)\b")
DIRECT_DEPLOY_SUFFIXES = {".sh", ".py", ".nix", ".md"}


def components() -> list[Path]:
    return sorted(p for p in COMPONENTS.iterdir() if p.is_dir()) if COMPONENTS.exists() else []


def purpose(c: Path) -> str:
    readme = c / "README.md"
    if not readme.exists():
        return ""
    for line in readme.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().lstrip("#").strip()
        if line and line.lower() != c.name:
            return line
    return ""


PRIVATE_PATTERNS = (
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("an API token", re.compile(r"\b(sk-[A-Za-z0-9_\-]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
                                r"AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,})")),
)
# A host file (nix/hosts/) describes one kind of machine and may name its network; nothing else does.
ADDRESS = re.compile(r"\b(10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)\b")
WHERE = "belongs in this install's local settings (/srv/odd1-local), About you or the secret store, not the shared repository"


def private_values() -> list[tuple[str, str]]:
    """(what, value) pairs that must never appear in the shared repository: the person's full name and email (About
    you, as installed and as they changed it) and every stored secret. Values are never printed."""
    import json
    import sqlite3
    out = []
    people = [os.environ.get("ODD1_CHECK_PERSON") or "/etc/odd1/person.json",
              os.path.expanduser("~/.config/odd1/person.json")]
    for path in people:
        try:
            person = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            continue
        if " " in (person.get("name") or "").strip():
            out.append(("the person's name", person["name"].strip()))
        if "@" in (person.get("email") or ""):
            out.append(("the person's email", person["email"].strip()))
    vault = os.environ.get("ODD1_CHECK_SECRETS") or "/var/lib/odd1/secrets/pods.db"
    try:
        db = sqlite3.connect(f"file:{vault}?mode=ro", uri=True)
        out += [(f"a stored secret ({uri})", value) for uri, value in db.execute("SELECT uri, value FROM values_")
                if len(value) >= 8]
    except sqlite3.Error:
        pass
    return out


def private_data(f: Path, rel: Path, private) -> list[str]:
    if not f.is_file() or f.stat().st_size > MAX_FILE_BYTES:
        return []
    try:
        text = f.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    found = [what for what, value in private if value in text]
    found += [what for what, pattern in PRIVATE_PATTERNS if pattern.search(text)]
    if rel.parts[:2] != ("nix", "hosts") and ADDRESS.search(text):
        found.append("a private network address")
    return [f"{rel}: holds {what}: it {WHERE}" for what in dict.fromkeys(found)]


def files() -> list[Path]:
    """What the repository holds or would take in a commit: tracked files and untracked ones git does not ignore (a
    test run's __pycache__ is ignored, so it is not the repository's problem). Every path without git."""
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return list(ROOT.rglob("*"))
    found = {ROOT / p for p in out.split("\0") if p}
    folders = {ROOT / d for f in found for d in f.relative_to(ROOT).parents if str(d) != "."}
    return sorted(found | folders)


def unit_names(module: Path) -> list[str]:
    """systemd unit names a module.nix declares (systemd.services.NAME / systemd.user.services.NAME)."""
    names = []
    for line in module.read_text(encoding="utf-8", errors="replace").splitlines():
        for marker in ("systemd.services.", "systemd.user.services."):
            if marker in line:
                rest = line.split(marker, 1)[1]
                name = rest.split("=", 1)[0].split(".", 1)[0].strip().strip('"')
                if name:
                    names.append(name)
    return names


def check() -> list[str]:
    problems = []
    units: dict[str, str] = {}
    for c in components():
        name = c.name
        if not set(name) <= NAME_CHARS or name.startswith("-"):
            problems.append(f"{name}: component names are lowercase-with-dashes")
        if not purpose(c):
            problems.append(f"{name}: README.md missing or has no purpose line")
        tests = c / "tests"
        if not ((tests.is_dir() and any(tests.iterdir())) or (c / "test.sh").exists()):
            problems.append(f"{name}: no tests (tests/ or test.sh)")
        lines = sum(len(f.read_text(encoding="utf-8", errors="replace").splitlines())
                    for f in c.rglob("*") if f.is_file() and f.suffix in SOURCE_SUFFIXES and "tests" not in f.parts)
        if lines > MAX_SOURCE_LINES:
            problems.append(f"{name}: {lines} source lines (over {MAX_SOURCE_LINES}): split it")
        module = c / "module.nix"
        if module.exists():
            for unit in unit_names(module):
                if unit in units:
                    problems.append(f"{name}: systemd unit {unit!r} is also declared by {units[unit]}")
                units[unit] = name
    private = private_values()
    for f in files():
        rel = f.relative_to(ROOT)
        if ".git" in rel.parts:
            continue
        if any(part in NEVER for part in rel.parts):
            problems.append(f"{rel}: caches and environments do not belong in the repository")
            continue
        if not f.is_file():
            continue
        if f.name.lower().endswith(NEVER_SUFFIXES):
            problems.append(f"{rel}: logs, images, media and build outputs do not belong in the repository")
        elif f.stat().st_size > MAX_FILE_BYTES:
            problems.append(f"{rel}: over {MAX_FILE_BYTES // 1_000_000} MB")
        elif f.suffix == ".md" and (f.stat().st_size > MAX_NOTE_BYTES or any(n in f.name.upper() for n in NOTE_NAMES)):
            problems.append(f"{rel}: notes stay short (under {MAX_NOTE_BYTES // 1000} KB, no transcripts or progress logs)")
        problems += private_data(f, rel, private)
        if f.suffix in DIRECT_DEPLOY_SUFFIXES and rel.parts[:2] != ("components", "deploy") and \
                DIRECT_DEPLOY.search(f.read_text(encoding="utf-8", errors="replace")):
            problems.append(f"{rel}: activates a system with nixos-rebuild directly; deploy with odd1-deploy "
                            "(it ships a clean release of a commit, never uncommitted work)")
    return problems


def main() -> int:
    if "--map" in sys.argv:
        for c in components():
            print(f"{c.name}: {purpose(c) or '(no purpose line)'}")
        return 0
    problems = check()
    for p in problems:
        print("PROBLEM", p)
    print(f"{len(components())} components, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
