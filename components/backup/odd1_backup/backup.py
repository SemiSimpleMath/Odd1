"""Local backups of Odd1's state and the person's files, so no accident destroys their data (rule 2).

- state: once a day, /var/lib/odd1 into <root>/<YYYY-MM-DD>/: each component's SQLite database as a consistent
  online backup named after the component (tasks/tasks.db -> tasks.db), everything else under files/. 14 days kept.
- home: every hour, the person's home folder into <root>/home/<YYYY-MM-DDTHH-MM>/, unchanged files hard-linked to
  the previous snapshot (rsync --link-dest), so an hour costs only what changed. Caches, package downloads, the
  Remote Desktop drives (~/Windows) and other regenerable things are left out. The last 48 snapshots are kept, and
  the last one of each day for 14 days.
- restore never destroys either: whatever is in the way is moved aside to <path>.before-restore-<time> first.

    odd1-backup run [state|home]             odd1-backup list
    odd1-backup restore DATE [--to DIR]      odd1-backup restore-file PATH [--at TIME]
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("ODD1_BACKUP_ROOT", "/var/backups/odd1"))
STATE = Path(os.environ.get("ODD1_BACKUP_STATE", "/var/lib/odd1"))
HOME = Path(os.environ.get("ODD1_BACKUP_HOME", Path.home()))
KEEP_DAYS = 14
KEEP_HOURLY = 48
# Left out of the state snapshot: rebuilt from git on demand (deploy releases) or the store's -wal/-shm companions.
# The secret store (#68) will keep its own encrypted backups; plain copies of it never go here.
STATE_EXCLUDE = ["/deploy/releases/", "/secrets/", "*.db-wal", "*.db-shm", "*.build"]
HOME_EXCLUDE = ["/Windows/", "/.cache/", "/.npm/", "/.local/lib/", "/.nix-defexpr/", "/.nix-profile", "/tmp/",
                "/.local/share/Trash/", "node_modules/", "__pycache__/", ".pytest_cache/", ".venv/", "*.db-wal",
                "*.db-shm"]
STAMP_HOME = "%Y-%m-%dT%H-%M"


class BackupError(Exception):
    pass


def _rsync(src: Path, dst: Path, exclude, link_dest: Path | None) -> None:
    cmd = ["rsync", "-a", "--delete", *(f"--exclude={e}" for e in exclude)]
    if link_dest:
        cmd.append(f"--link-dest={link_dest}")
    p = subprocess.run(cmd + [f"{src}/", f"{dst}/"], capture_output=True, text=True)
    if p.returncode not in (0, 24):  # 24: files vanished while copying (a live system), not an error
        raise BackupError(f"rsync {src}: {p.stderr.strip()[-500:]}")


def _db_copy(src: Path, dst: Path) -> None:
    """A consistent copy of a live SQLite database (the online backup API), whatever its writers are doing."""
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(dst)
    with target:
        source.backup(target)
    source.close()
    target.close()


def state_snapshots(root: Path = ROOT) -> list[Path]:
    return sorted(p for p in root.glob("????-??-??") if p.is_dir()) if root.is_dir() else []


def home_snapshots(root: Path = ROOT) -> list[Path]:
    where = root / "home"
    return sorted(p for p in where.glob("????-??-??T??-??") if p.is_dir()) if where.is_dir() else []


def _finish(tmp: Path, final: Path) -> Path:
    if final.exists():  # a second run in the same period replaces the earlier one
        shutil.rmtree(final)
    tmp.rename(final)
    return final


def snapshot_state(root: Path = ROOT, state: Path = STATE, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    final = root / now.strftime("%Y-%m-%d")
    tmp = root / f".tmp-{final.name}-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "files").mkdir(parents=True)
    earlier = [p for p in state_snapshots(root) if p != final]
    _rsync(state, tmp / "files", STATE_EXCLUDE + ["*.db"], earlier[-1] / "files" if earlier else None)
    for db in sorted(state.glob("*/*.db")):
        _db_copy(db, tmp / f"{db.parent.name}.db")
    (tmp / "SNAPSHOT").write_text(f"state of {state} at {now.isoformat(timespec='seconds')}\n")
    return _finish(tmp, final)


def snapshot_home(root: Path = ROOT, home: Path = HOME, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    final = root / "home" / now.strftime(STAMP_HOME)
    tmp = root / "home" / f".tmp-{final.name}-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    earlier = [p for p in home_snapshots(root) if p != final]
    _rsync(home, tmp, HOME_EXCLUDE, earlier[-1] if earlier else None)
    return _finish(tmp, final)


def prune(root: Path = ROOT, now: datetime | None = None) -> list[Path]:
    """Drop state snapshots older than KEEP_DAYS, and home snapshots beyond the last KEEP_HOURLY except each day's
    last for KEEP_DAYS. Returns what it removed."""
    now = now or datetime.now()
    gone = []
    for p in state_snapshots(root):
        if (now - datetime.strptime(p.name, "%Y-%m-%d")).days >= KEEP_DAYS:
            gone.append(p)
    homes = home_snapshots(root)
    days_last = {}
    for p in homes:
        days_last[p.name[:10]] = p
    for p in homes[:-KEEP_HOURLY]:
        day_ok = days_last[p.name[:10]] == p and (now - datetime.strptime(p.name[:10], "%Y-%m-%d")).days < KEEP_DAYS
        if not day_ok:
            gone.append(p)
    for p in gone:
        shutil.rmtree(p)
    return gone


def _aside(path: Path) -> Path | None:
    """Move whatever is at path out of the way (never delete it). Returns where it went."""
    if not path.exists() and not path.is_symlink():
        return None
    aside = path.with_name(f"{path.name}.before-restore-{time.strftime('%Y%m%d-%H%M%S')}")
    path.rename(aside)
    return aside


def restore_state(date: str, to: Path = STATE, root: Path = ROOT) -> dict:
    """Put the state snapshot of DATE back at `to` (a scratch folder, or /var/lib/odd1 with its services stopped)."""
    snap = root / date
    if not (snap / "SNAPSHOT").exists():
        raise BackupError(f"no state snapshot {date} (odd1-backup list)")
    aside = _aside(to)
    shutil.copytree(snap / "files", to, symlinks=True)
    for db in snap.glob("*.db"):
        (to / db.stem).mkdir(parents=True, exist_ok=True)
        shutil.copy2(db, to / db.stem / db.name)
    return {"restored": str(to), "from": str(snap), "moved_aside": str(aside) if aside else None}


def restore_file(path: Path, at: datetime | None = None, home: Path = HOME, root: Path = ROOT) -> dict:
    """Put back the version of a file or folder under home from the newest home snapshot at or before `at`."""
    path = Path(os.path.abspath(path))
    try:
        rel = path.relative_to(home)
    except ValueError:
        raise BackupError(f"{path} is not under {home}") from None
    at = at or datetime.now()
    snaps = [p for p in home_snapshots(root) if datetime.strptime(p.name, STAMP_HOME) <= at and (p / rel).exists()]
    if not snaps:
        raise BackupError(f"no snapshot at or before {at:%Y-%m-%d %H:%M} has {rel}")
    src = snaps[-1] / rel
    aside = _aside(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, path, symlinks=True)
    else:
        shutil.copy2(src, path)
    return {"restored": str(path), "from": str(src), "moved_aside": str(aside) if aside else None}
