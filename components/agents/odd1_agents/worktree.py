"""Each chat changes the repository in its own git worktree, never in the shared checkout (/srv/odd1).

The shared checkout is main as it landed; chats that edited it directly saw and committed each other's unfinished
edits (2026-10-15: a chat's commit swept in another chat's change to layout.py). A Claude Code hook (PreToolUse, in
the managed settings, nix/odd1.nix) runs `odd1-agents guard` before every edit and command: an edit of a file in
the shared checkout, or a `git add`/`git commit` there, is refused with the same file's path in this chat's own
worktree, which it makes on the spot: branch chat/<session>, from main, in /var/lib/odd1/chats/<session>. The chat
lands from there with `odd1-chat-land` (rebase, odd1-tasks-check, fast-forward main, odd1-deploy), as roles do.

Role runs (fixer, updater) have worktrees of their own (odd1_tasks/workspace.py) and are left alone; a chat started
with ODD1_SHARED_CHECKOUT=1 edits the shared checkout as before.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

REPO = Path(os.environ.get("ODD1_REPO", "/srv/odd1"))
CHATS = Path(os.environ.get("ODD1_CHATS", "/var/lib/odd1/chats"))
EDITS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
# git add/commit (also as `git -C DIR ...`), the commands that took another chat's edits along.
GIT_RECORD = re.compile(r"\bgit\b(?:\s+-C\s+(\S+)|\s+-c\s+\S+|\s+--?[\w-]+(?:=\S+)?)*\s+(add|commit)\b")
IDLE = 12 * 3600  # a landed, clean worktree untouched this long is removed


def _git(*args, cwd=None, check=True) -> str:
    return subprocess.run(["git", *args], cwd=cwd or REPO, check=check, capture_output=True, text=True).stdout.strip()


def name(session: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "", session)[:8] or "chat"


def branch(session: str) -> str:
    return f"chat/{name(session)}"


def path(session: str) -> Path:
    return CHATS / name(session)


def prepare(session: str) -> Path:
    """This chat's worktree, made from main if it does not exist yet (its branch is reused if it does)."""
    where = path(session)
    if (where / ".git").exists():
        return where
    tidy()
    where.parent.mkdir(parents=True, exist_ok=True)
    if _git("branch", "--list", branch(session)):
        _git("worktree", "add", str(where), branch(session))
    else:
        _git("worktree", "add", "-b", branch(session), str(where), "main")
    return where


def tidy(now: float | None = None) -> list[str]:
    """Remove chat worktrees with nothing left to land: clean, their branch in main, untouched for IDLE."""
    now = now or time.time()
    removed = []
    for where in sorted(CHATS.iterdir()) if CHATS.is_dir() else []:
        try:
            ref = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=where)
            if not ref.startswith("chat/") or _git("status", "--porcelain", cwd=where):
                continue
            if now - where.stat().st_mtime < IDLE:
                continue
            merged = subprocess.run(["git", "merge-base", "--is-ancestor", ref, "main"], cwd=REPO).returncode == 0
            if merged:
                _git("worktree", "remove", str(where))
                _git("branch", "-d", ref, check=False)
                removed.append(str(where))
        except (subprocess.CalledProcessError, OSError):
            continue
    return removed


def _inside(file: str, cwd: str) -> str | None:
    """The file's path relative to the shared checkout, if it is in it."""
    full = os.path.realpath(os.path.join(cwd or "/", os.path.expanduser(file)))
    repo = os.path.realpath(REPO)
    return os.path.relpath(full, repo) if full == repo or full.startswith(repo + os.sep) else None


def _records_in_shared(command: str, cwd: str) -> bool:
    """A git add/commit that would run in the shared checkout."""
    for match in GIT_RECORD.finditer(command):
        target = match.group(1)
        if target:
            if _inside(target, cwd) == ".":
                return True
        elif _inside(cwd, "/") is not None and not re.search(r"(^|[;&|(]\s*)cd\s", command[:match.start()]):
            return True
    return False


def decide(event: dict, environ=os.environ) -> str | None:
    """The reason to refuse this tool call (PreToolUse input), or None to let it run."""
    if environ.get("ODD1_TASKS_ROLE_FOLDER") or environ.get("ODD1_SHARED_CHECKOUT") == "1":
        return None
    tool, given = event.get("tool_name"), event.get("tool_input") or {}
    cwd, session = event.get("cwd") or "/", event.get("session_id") or ""
    if tool in EDITS:
        rel = _inside(given.get(EDITS[tool]) or "", cwd)
        if rel is None or rel == "." or rel.split(os.sep)[0] == ".git":
            return None
        where = prepare(session)
        return (f"{REPO} is the shared checkout (main as landed; other sessions read it), so this chat changes the "
                f"repository in its own git worktree: {where} (branch {branch(session)}, from main). Edit "
                f"{where / rel} instead, and work there from now on: read, test, git and `python tools/check.py` "
                f"in {where}. Land it with `cd {where} && odd1-chat-land [COMPONENT...]` (rebase, odd1-tasks-check, "
                f"main, odd1-deploy). (components/agents: worktree.py)")
    if tool == "Bash" and _records_in_shared(given.get("command") or "", cwd):
        where = prepare(session)
        return (f"git add/commit in the shared checkout {REPO} would take other sessions' uncommitted edits along. "
                f"This chat's own worktree is {where} (branch {branch(session)}): make the change there, commit "
                f"there, and land with `cd {where} && odd1-chat-land`. Edits already made in {REPO} can be moved "
                f"with `git -C {REPO} diff -- FILE | git -C {where} apply`, then `git -C {REPO} checkout -- FILE` "
                f"(only the files this chat changed). (components/agents: worktree.py)")
    return None


def guard(stdin, stdout, environ=os.environ) -> int:
    """The PreToolUse hook: refuse with the way round, or say nothing. Never stops a chat over its own failure."""
    try:
        reason = decide(json.load(stdin), environ)
    except Exception:  # noqa: BLE001  a broken guard must not break every chat
        return 0
    if reason:
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                          "permissionDecisionReason": reason}}, stdout)
    return 0
