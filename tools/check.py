"""Odd1 fitness check: keeps the repository in one shape as it grows. Run before every commit.

    python tools/check.py          check; exit 1 on any problem
    python tools/check.py --map    print the component map (name: first README line)

Plain string checks only. A problem is a prompt to split, merge or delete, not to weaken this file.
"""
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
    for f in ROOT.rglob("*"):
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
