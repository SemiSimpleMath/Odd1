"""Chats change the repository in their own worktree: the guard against a throwaway repository."""
import io
import json
import os
import subprocess

import pytest

from odd1_agents import worktree

SESSION = "3a3f35df-0413-49dc-9af5-dfc449f63427"


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repo = tmp_path / "odd1"
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    (repo / "README.md").write_text("odd1\n")
    git("add", "README.md", cwd=repo)
    git("commit", "-qm", "start", cwd=repo)
    monkeypatch.setattr(worktree, "REPO", repo)
    monkeypatch.setattr(worktree, "CHATS", tmp_path / "chats")
    return repo


def edit(path, cwd="/"):
    return {"tool_name": "Edit", "tool_input": {"file_path": str(path)}, "cwd": cwd, "session_id": SESSION}


def bash(command, cwd):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd), "session_id": SESSION}


def test_an_edit_in_the_shared_checkout_goes_to_the_chats_worktree(repo, tmp_path):
    reason = worktree.decide(edit(repo / "README.md"), {})
    where = tmp_path / "chats" / "3a3f35df"
    assert str(where / "README.md") in reason and "odd1-chat-land" in reason
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=where) == "chat/3a3f35df"
    assert worktree.decide(edit(where / "README.md"), {}) is None  # its own worktree is free
    assert worktree.decide(edit("README.md", cwd=str(repo)), {}) is not None  # relative paths too


def test_edits_elsewhere_roles_and_opt_out_pass(repo, tmp_path):
    assert worktree.decide(edit(tmp_path / "notes.md"), {}) is None
    assert worktree.decide(edit(repo / "README.md"), {"ODD1_TASKS_ROLE_FOLDER": "/x"}) is None
    assert worktree.decide(edit(repo / "README.md"), {"ODD1_SHARED_CHECKOUT": "1"}) is None
    assert not (tmp_path / "chats").exists()  # nothing made for them


@pytest.mark.parametrize("command,refused", [
    ("git commit -am x", True),
    ("git -c user.email=a@b commit -m x", True),
    ("git add components/x.py && git status", True),
    ("git -C {repo} commit -m x", True),
    ("git status && git log --oneline -3", False),
    ("git -C {repo} merge --ff-only chat/3a3f35df", False),
    ("cd {chats}/3a3f35df && git commit -am x", False),
    ("git -C {chats}/3a3f35df commit -am x", False),
])
def test_git_add_and_commit_in_the_shared_checkout_are_refused(repo, tmp_path, command, refused):
    command = command.format(repo=repo, chats=tmp_path / "chats")
    assert (worktree.decide(bash(command, repo), {}) is not None) == refused
    assert worktree.decide(bash("git commit -am x", tmp_path), {}) is None  # another repository


def test_the_hook_answers_claude_code_and_never_breaks_a_chat(repo):
    out = io.StringIO()
    assert worktree.guard(io.StringIO(json.dumps(edit(repo / "README.md"))), out, {}) == 0
    answer = json.loads(out.getvalue())["hookSpecificOutput"]
    assert answer["permissionDecision"] == "deny" and answer["hookEventName"] == "PreToolUse"
    out = io.StringIO()
    assert worktree.guard(io.StringIO("not json"), out, {}) == 0 and out.getvalue() == ""


def test_tidy_removes_only_landed_clean_idle_worktrees(repo, tmp_path):
    landed = worktree.prepare(SESSION)
    busy = worktree.prepare("ffffffff-busy")
    (busy / "new.txt").write_text("unfinished\n")
    later = os.path.getmtime(landed) + worktree.IDLE + 1
    assert worktree.tidy(now=later - 2 * worktree.IDLE) == []  # too recent
    assert worktree.tidy(now=later) == [str(landed)]
    assert busy.exists() and not git("branch", "--list", "chat/3a3f35df", cwd=repo)
