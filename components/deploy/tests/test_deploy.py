"""odd1-deploy against a throwaway repository and a fake nixos-rebuild that records what it was given to build."""
import json
import subprocess
import threading

import pytest

from odd1_deploy import cli
from odd1_deploy.deploy import Deployer, DeployError

FAKE_REBUILD = r"""
log=$1; action=$2; cfg=${4#nixos-config=}; root=${cfg%/nix/hosts/test.nix}
echo "start $action $(cat "$root/app.txt") $(ls -A "$root" | tr '\n' ' ')" >> "$log"
sleep "${FAKE_SLEEP:-0}"
echo "end" >> "$log"
[ ! -e "$root/broken" ]
"""


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, text, name="app.txt", message=None):
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-qm", message or f"{name}: {text}")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path, monkeypatch):
    repo = tmp_path / "odd1"
    (repo / "nix/hosts").mkdir(parents=True)
    git(tmp_path, "init", "-q", "-b", "main", str(repo))
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "nix/hosts/test.nix").write_text("{ }\n")
    git(repo, "add", ".")
    first = commit(repo, "v1")
    script = tmp_path / "rebuild.sh"
    script.write_text(FAKE_REBUILD)
    log = tmp_path / "rebuild.log"
    config = tmp_path / "configuration.nix"
    config.write_text(f"{{ imports = [ {repo}/nix/hosts/test.nix ]; }}\n")  # as the machine was before
    d = Deployer(state=tmp_path / "state", repo=repo, nixos_config=config, rebuild=f"sh {script} {log}", sudo="")
    for key, value in (("ODD1_DEPLOY_STATE", d.state), ("ODD1_REPO", repo), ("ODD1_DEPLOY_NIXOS_CONFIG", config),
                       ("ODD1_DEPLOY_REBUILD", f"sh {script} {log}"), ("ODD1_DEPLOY_SUDO", "")):
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv("ODD1_DEPLOY_HOST", raising=False)
    return {"repo": repo, "d": d, "log": log, "config": config, "first": first, "tmp": tmp_path}


def builds(world):
    return [line for line in world["log"].read_text().splitlines() if line.startswith("start")]


def snapshot(repo):
    """Everything that is someone's pending work in a checkout: the tree, the index, stashes, branches, worktrees."""
    return {"status": git(repo, "status", "--porcelain=v2", "--untracked-files=all"),
            "diff": git(repo, "diff"), "staged": git(repo, "diff", "--cached"), "stash": git(repo, "stash", "list"),
            "branches": git(repo, "branch", "-av"), "worktrees": git(repo, "worktree", "list", "--porcelain"),
            "head": git(repo, "rev-parse", "HEAD"), "app": (repo / "app.txt").read_text()}


def test_committed_changes_ship_and_uncommitted_ones_do_not(world):
    repo, d = world["repo"], world["d"]
    second = commit(repo, "committed v2")
    # Pending work of agents and sessions: an edit, a staged file, an untracked file, a stash, a task worktree.
    (repo / "other.txt").write_text("staged, not committed")
    git(repo, "add", "other.txt")
    (repo / "untracked.txt").write_text("untracked")
    (repo / "stashed.txt").write_text("x")
    git(repo, "add", "stashed.txt")
    git(repo, "stash", "-q")
    git(repo, "worktree", "add", "-q", "-b", "task/7", str(world["tmp"] / "task-7"), "main")
    (world["tmp"] / "task-7/app.txt").write_text("a task's unfinished edit")
    (repo / "app.txt").write_text("uncommitted edit")
    before = snapshot(repo)
    task_before = (world["tmp"] / "task-7/app.txt").read_text()

    record = d.deploy()

    assert record["ok"] and record["commit"] == second and d.current() == second
    [built] = builds(world)
    assert built.startswith("start switch committed v2 ")
    for leaked in ("other.txt", "untracked.txt", "stashed.txt", ".git "):
        assert leaked not in built + " "
    assert snapshot(repo) == before and (world["tmp"] / "task-7/app.txt").read_text() == task_before
    release = d.state / "releases" / second
    assert (release / "app.txt").read_text() == "committed v2"
    with pytest.raises(PermissionError):
        (release / "app.txt").write_text("edited in place")


def test_the_running_system_imports_the_release_not_the_development_folder(world):
    d = world["d"]
    d.deploy()
    text = world["config"].read_text()
    assert f"{d.state}/current/nix/hosts/test.nix" in text and str(world["repo"]) not in text
    assert (d.state / "current").resolve() == (d.state / "releases" / world["first"]).resolve()
    assert d.status()["configuration_uses_release"] and d.host() == "nix/hosts/test.nix"


def test_a_specific_commit_deploys_and_one_off_main_is_refused(world):
    repo, d = world["repo"], world["d"]
    commit(repo, "v2")
    assert d.deploy(world["first"])["commit"] == world["first"]
    assert "start switch v1 " in builds(world)[0] + " "
    git(repo, "checkout", "-q", "-b", "unreviewed")
    side = commit(repo, "not approved")
    git(repo, "checkout", "-q", "main")
    with pytest.raises(DeployError, match="not on main"):
        d.deploy(side)
    assert d.current() == world["first"] and len(builds(world)) == 1


def test_a_failed_switch_keeps_the_running_release_and_configuration(world):
    repo, d = world["repo"], world["d"]
    d.deploy()
    config = world["config"].read_text()
    commit(repo, "", name="broken")
    with pytest.raises(DeployError, match="failed"):
        d.deploy()
    assert d.current() == world["first"] and world["config"].read_text() == config
    assert [h["ok"] for h in d.history()] == [True, False]


def test_rollback_deploys_the_previous_commit_again(world):
    repo, d = world["repo"], world["d"]
    d.deploy()
    second = commit(repo, "v2")
    d.deploy()
    record = d.rollback()
    assert record["commit"] == world["first"] and d.current() == world["first"]
    assert builds(world)[-1].startswith("start switch v1 ") and "rollback from" in record["reason"]
    assert d.rollback()["commit"] == second  # and forward again
    with pytest.raises(DeployError, match="nothing to roll back"):
        Deployer(state=world["tmp"] / "fresh", repo=repo, nixos_config=world["config"], sudo="",
                 rebuild="true").rollback()


def test_deploys_are_serialized(world, monkeypatch):
    repo, d = world["repo"], world["d"]
    commit(repo, "v2")
    monkeypatch.setenv("FAKE_SLEEP", "0.4")
    errors = []

    def run():
        try:
            Deployer(state=d.state, repo=repo, nixos_config=world["config"], rebuild=" ".join(d.rebuild),
                     sudo="").deploy()
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = [line.split()[0] for line in world["log"].read_text().splitlines()]
    assert not errors and lines == ["start", "end"] * 3


def test_no_wait_fails_while_another_deploy_runs(world):
    d = world["d"]
    with d._lock():
        with pytest.raises(DeployError, match="another deploy"):
            d.deploy(wait=False)


def test_old_releases_are_pruned(world):
    repo, d = world["repo"], world["d"]
    shas = [d.deploy()["commit"]]
    for i in range(7):
        commit(repo, f"v{i + 2}")
        shas.append(d.deploy()["commit"])
    kept = {p.name for p in (d.state / "releases").iterdir()}
    assert kept == set(shas[-5:])


def test_cli_status_names_the_deployed_commit(world, capsys):
    assert cli.main([]) == 0
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"current {world['first'][:12]} ") and "imports the deployed release" in out
    assert cli.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["current"] == world["first"]
    assert cli.main(["host"]) == 0 and capsys.readouterr().out == "nix/hosts/test.nix\n"
    assert cli.main(["no-such-commit"]) == 1
