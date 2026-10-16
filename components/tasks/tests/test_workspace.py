"""Roles that change the repository: a git worktree per task, the role's tags for every component, its environment."""
import json
import subprocess
from pathlib import Path

import pytest
from conftest import PERSON, approved

from odd1_tasks import roles, runner, workspace


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "components" / "desktop").mkdir(parents=True)
    (repo / "components" / "desktop" / "README.md").write_text("desktop\n")
    git("init", "-q", "-b", "main", cwd=repo)
    git("-c", "user.name=t", "-c", "user.email=t@t", "add", ".", cwd=repo)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "start", cwd=repo)
    monkeypatch.setattr(workspace, "REPO", repo)
    return repo


def test_the_fixer_holds_a_tag_for_every_component(store, repo):
    roles.seed(store, "fixer")
    assert roles.load(store, "fixer")["tags"] == ["desktop", "friction", "odd1"]
    ticket = approved(store, "A desktop ticket", tags=["friction", "desktop"])
    assert store.claim("fixer")["id"] == ticket["id"]


def test_a_task_runs_in_its_own_worktree_and_leaves_its_branch(store, repo, monkeypatch, tmp_path):
    roles.seed(store, "fixer")
    marker = tmp_path / "ran-in"
    ticket = approved(store, "Fix it", tags=["friction", "desktop"], done_when="true")
    monkeypatch.setenv("STUB_PLAN", json.dumps({"finish": True}))
    task = runner.run_once(store, "fixer")
    call = [json.loads(line) for line in open(tmp_path / "stub.jsonl")][-1]
    where = workspace.path(store.state, ticket["id"])
    assert call["cwd"] == str(where)  # the stub ran in the task's worktree, not in the shared checkout
    args = call["argv"]
    assert args[args.index("--append-system-prompt-file") + 1].endswith("roles/fixer/CLAUDE.md")
    assert f"Your workspace: {where} (git branch task/{ticket['id']}" in call["prompt"]
    assert task["status"] == "done" and not where.exists()  # closed: the worktree goes, the branch stays
    assert git("branch", "--list", f"task/{ticket['id']}", cwd=repo)
    assert not marker.exists()


def test_an_interrupted_task_resumes_in_the_same_worktree(store, repo):
    first = workspace.prepare(store.state, 7, repo)
    (first / "half-done.txt").write_text("work in progress")
    assert workspace.prepare(store.state, 7, repo) == first and (first / "half-done.txt").exists()
    workspace.remove(store.state, 7, repo)
    again = workspace.prepare(store.state, 7, repo)  # the branch is reused
    assert git("branch", "--show-current", cwd=again) == "task/7"


def test_closed_tasks_lose_their_worktrees_on_the_next_loop(store, repo):
    roles.seed(store, "fixer")
    t = approved(store, "Fix it", tags=["friction", "desktop"])
    workspace.prepare(store.state, t["id"], repo)
    store.cancel(t["id"], PERSON, "not needed")
    workspace.tidy(store, repo)
    assert not workspace.path(store.state, t["id"]).exists()


def test_land_refuses_while_the_role_asks_for_review(store, repo, tmp_path):
    roles.seed(store, "fixer")
    folder = roles.folder(store, "fixer")
    toml = folder / "role.toml"
    toml.write_text(toml.read_text().replace("review = false", "review = true"))
    where = workspace.prepare(store.state, 9, repo)
    land = Path(__file__).resolve().parents[1] / "scripts" / "land.sh"
    done = subprocess.run(["bash", str(land)], cwd=where, capture_output=True, text=True,
                          env={"PATH": "/run/current-system/sw/bin:/usr/bin:/bin", "ODD1_TASKS_WORKTREE": str(where),
                               "ODD1_TASKS_ROLE_FOLDER": str(folder), "ODD1_REPO": str(repo)})
    assert done.returncode == 3 and "review" in done.stderr
    assert git("rev-parse", "main", cwd=repo) == git("rev-parse", "HEAD", cwd=where)  # nothing landed
