"""Roles: one folder per agent role under <state>/roles/<role>/, seeded from the repository and then the person's.

A role folder holds CLAUDE.md (the role's instructions), settings.json (Claude Code permissions: allowed tools and
paths), role.toml (tags, budgets, starter tasks; read on every runner loop, so edits need no rebuild), any helper the
role's permissions name, and reports/ for its output.
"""
from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path

from .store import Store

SEED = Path(os.environ.get("ODD1_TASKS_ROLES_SEED", Path(__file__).resolve().parent.parent / "roles"))
DEFAULTS = {"tags": [], "runs_per_day": 4, "max_turns": 20, "max_minutes": 15, "model": "", "starter": []}


def folder(store: Store, role: str) -> Path:
    return store.state / "roles" / role


def load(store: Store, role: str) -> dict:
    path = folder(store, role) / "role.toml"
    if not path.exists():
        raise FileNotFoundError(f"role {role!r} has no {path}")
    cfg = dict(DEFAULTS, **tomllib.loads(path.read_text()))
    cfg["name"], cfg["folder"] = role, path.parent
    return cfg


def copy_missing(src: Path, dst: Path) -> None:
    """Copy src's files into dst where dst has none yet, writable (files from the Nix store are read-only)."""
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.rglob("*") if src.is_dir() else []:
        target = dst / f.relative_to(src)
        if f.is_dir() or target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, target)
        target.chmod(0o755 if os.access(f, os.X_OK) else 0o644)


def seed(store: Store, role: str, source: Path = SEED) -> dict:
    """Copy the role's files from the repository where missing (the person's edits are kept), register the role's
    tags with the store, and create its starter tasks once, as proposed."""
    dst = folder(store, role)
    copy_missing(source / role, dst)
    (dst / "reports").mkdir(parents=True, exist_ok=True)
    cfg = load(store, role)
    store.register_agent(role, cfg["tags"])
    made = {t["goal"] for t in store.list() if t["created_by"] == f"seed:{role}"}
    for s in cfg["starter"]:
        if s["goal"] not in made:
            store.create(s["goal"], s["done_when"], s.get("tags", cfg["tags"]), actor=f"seed:{role}",
                         priority=s.get("priority", 0), resources=s.get("resources", []),
                         schedule=s.get("schedule"), max_turns=s.get("max_turns"), max_minutes=s.get("max_minutes"))
    return cfg
