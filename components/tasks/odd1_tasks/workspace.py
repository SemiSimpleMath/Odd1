"""A task's own git worktree, for roles that change the repository (role.toml: workspace = "git-worktree").

Each task works on branch task/<id> in <state>/work/task-<id>, made from main, never in the shared checkout
(/srv/odd1), so it neither sees nor ships anyone's uncommitted edits. The worktree survives an interrupted run (the
next run resumes in it) and is removed once the task is closed; the branch stays, as the record of the work.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(os.environ.get("ODD1_REPO", "/srv/odd1"))


def _git(*args, cwd=None) -> str:
    return subprocess.run(["git", *args], cwd=cwd or REPO, check=True, capture_output=True, text=True).stdout.strip()


def path(state: Path, task_id: int) -> Path:
    return state / "work" / f"task-{task_id}"


def branch(task_id: int) -> str:
    return f"task/{task_id}"


def prepare(state: Path, task_id: int, repo: Path | None = None) -> Path:
    """The task's worktree, made from main if it does not exist yet."""
    repo = repo or REPO
    where = path(state, task_id)
    if (where / ".git").exists():
        return where
    where.parent.mkdir(parents=True, exist_ok=True)
    known = _git("branch", "--list", branch(task_id), cwd=repo)
    if known:
        _git("worktree", "add", str(where), branch(task_id), cwd=repo)
    else:
        _git("worktree", "add", "-b", branch(task_id), str(where), "main", cwd=repo)
    return where


def remove(state: Path, task_id: int, repo: Path | None = None) -> None:
    repo = repo or REPO
    where = path(state, task_id)
    if where.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(where)], cwd=repo, capture_output=True)


def tidy(store, repo: Path | None = None) -> None:
    """Remove the worktrees of tasks that are closed (done, declined, cancelled, parked)."""
    from .store import CLOSED
    folder = store.state / "work"
    for where in folder.glob("task-*") if folder.is_dir() else []:
        try:
            task_id = int(where.name.split("-", 1)[1])
            if store.show(task_id)["status"] in CLOSED:
                remove(store.state, task_id, repo)
        except (ValueError, Exception):
            continue
