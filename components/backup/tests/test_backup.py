"""Backups against throwaway folders: a consistent database copy that the task store opens after a restore, hourly
home snapshots that share unchanged files, restores that move things aside instead of deleting, and retention."""
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from odd1_backup import backup, cli

TASKS = Path(__file__).resolve().parents[2] / "tasks"


@pytest.fixture
def world(tmp_path):
    state, home, root = tmp_path / "state", tmp_path / "home", tmp_path / "backups"
    (state / "tasks" / "roles" / "fixer").mkdir(parents=True)
    (state / "tasks" / "roles" / "fixer" / "role.toml").write_text("tags = ['friction']\n")
    db = sqlite3.connect(state / "tasks" / "tasks.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE t(x)")
    db.execute("INSERT INTO t VALUES (42)")
    db.commit()  # left open, with its -wal: a live store
    (state / "deploy" / "releases" / "abc").mkdir(parents=True)
    (state / "deploy" / "releases" / "abc" / "big").write_text("rebuilt from git")
    (home / "Documents").mkdir(parents=True)
    (home / "Documents" / "plan.txt").write_text("v1")
    (home / ".cache").mkdir()
    (home / ".cache" / "junk").write_text("x")
    (home / "Windows" / "E:").mkdir(parents=True)
    (home / "Windows" / "E:" / "remote").write_text("the person's other computer")
    return {"state": state, "home": home, "root": root, "db": db}


def test_state_snapshot_restores_and_the_task_store_opens_it(world, tmp_path):
    snap = backup.snapshot_state(world["root"], world["state"])
    assert (snap / "tasks.db").exists() and not (snap / "files" / "tasks" / "tasks.db").exists()
    assert not (snap / "files" / "deploy" / "releases").exists()
    scratch = tmp_path / "restored"
    out = backup.restore_state(snap.name, scratch, world["root"])
    assert out["moved_aside"] is None
    assert sqlite3.connect(scratch / "tasks" / "tasks.db").execute("SELECT x FROM t").fetchall() == [(42,)]
    assert (scratch / "tasks" / "roles" / "fixer" / "role.toml").read_text() == "tags = ['friction']\n"
    sys.path.insert(0, str(TASKS))
    from odd1_tasks.store import Store
    Store(scratch / "tasks")  # the real task store opens (and migrates) a restored database


def test_restoring_over_existing_state_moves_it_aside(world):
    snap = backup.snapshot_state(world["root"], world["state"])
    (world["state"] / "tasks" / "newer").write_text("made after the snapshot")
    out = backup.restore_state(snap.name, world["state"], world["root"])
    assert Path(out["moved_aside"], "tasks", "newer").read_text() == "made after the snapshot"


def test_home_snapshots_leave_out_caches_and_remote_drives_and_share_unchanged_files(world):
    t0 = datetime(2026, 10, 16, 1, 0)
    first = backup.snapshot_home(world["root"], world["home"], t0)
    assert (first / "Documents" / "plan.txt").read_text() == "v1"
    assert not (first / ".cache").exists() and not (first / "Windows").exists()
    second = backup.snapshot_home(world["root"], world["home"], t0 + timedelta(hours=1))
    assert os.stat(first / "Documents" / "plan.txt").st_ino == os.stat(second / "Documents" / "plan.txt").st_ino


def test_home_a_deleted_file_comes_back_as_it_was_and_the_newer_one_is_kept_aside(world):
    t0 = datetime.now().replace(second=0, microsecond=0) - timedelta(hours=3)
    plan = world["home"] / "Documents" / "plan.txt"
    backup.snapshot_home(world["root"], world["home"], t0)
    plan.write_text("v2")
    os.utime(plan, (time.time() + 5, time.time() + 5))  # rsync compares size and mtime; a real edit is seconds later
    backup.snapshot_home(world["root"], world["home"], t0 + timedelta(hours=1))
    plan.unlink()  # an accident
    out = backup.restore_file(plan, None, world["home"], world["root"])
    assert plan.read_text() == "v2" and out["moved_aside"] is None
    out = backup.restore_file(plan, t0 + timedelta(minutes=30), world["home"], world["root"])
    assert plan.read_text() == "v1" and Path(out["moved_aside"]).read_text() == "v2"
    with pytest.raises(backup.BackupError, match="not under"):
        backup.restore_file(Path("/etc/passwd"), None, world["home"], world["root"])


def test_retention_keeps_two_weeks_of_state_and_hourly_then_daily_home(world):
    now = datetime(2026, 10, 30, 12, 0)
    for d in range(20):
        backup.snapshot_state(world["root"], world["state"], now - timedelta(days=d))
    for h in range(24 * 4):
        (world["root"] / "home" / (now - timedelta(hours=h)).strftime(backup.STAMP_HOME)).mkdir(parents=True)
    backup.prune(world["root"], now)
    assert len(backup.state_snapshots(world["root"])) == 14
    homes = [p.name for p in backup.home_snapshots(world["root"])]
    assert len(homes) == 48 + 2  # the last 48 hours, plus the last snapshot of each older day still in two weeks
    assert (now - timedelta(hours=47)).strftime(backup.STAMP_HOME) in homes


def test_cli_lists_and_reports_errors(world, monkeypatch, capsys):
    monkeypatch.setattr(backup, "ROOT", world["root"])
    backup.snapshot_state(world["root"], world["state"])
    monkeypatch.setattr(backup.state_snapshots, "__defaults__", (world["root"],))
    monkeypatch.setattr(backup.home_snapshots, "__defaults__", (world["root"],))
    assert cli.main(["list"]) == 0 and "state " in capsys.readouterr().out
    assert cli.main(["restore", "1999-01-01", "--to", str(world["root"] / "x")]) == 1
