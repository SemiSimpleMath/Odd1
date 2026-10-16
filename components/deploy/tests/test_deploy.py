"""odd1-deploy against a throwaway repository and a fake nixos-rebuild that records what it was given to build."""
import json
import subprocess
import threading

import pytest

from odd1_deploy import cli
from odd1_deploy.deploy import Deployer, DeployError

FAKE_REBUILD = r"""
log=$1; action=$2; cfg=${4#nixos-config=}
case $cfg in */odd1-config.nix) root=${cfg%/odd1-config.nix} ;; *) root=${cfg%/nix/hosts/test.nix} ;; esac
echo "start $action $(cat "$root/app.txt") $(ls -A "$root" | tr '\n' ' ') local:$(cat "$root/local/host.nix" 2>/dev/null)" >> "$log"
sleep "${FAKE_SLEEP:-0}"
echo "end" >> "$log"
cp "$root/wants" "$FAKE_WANTS" 2>/dev/null || : > "$FAKE_WANTS"  # the user units the commit's tree wants
[ ! -e "$root/broken" ]
"""

# `systemctl --user`: default.target wants what the last switch installed; starts are logged.
FAKE_USER_SYSTEMCTL = r"""
case $1 in show) cat "$FAKE_WANTS" ;; start) echo "$2" >> "$FAKE_STARTED" ;; esac
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
    user_systemctl = tmp_path / "systemctl-user.sh"
    user_systemctl.write_text(FAKE_USER_SYSTEMCTL)
    (tmp_path / "wants").write_text("")
    config = tmp_path / "configuration.nix"
    config.write_text(f"{{ imports = [ {repo}/nix/hosts/test.nix ]; }}\n")  # as the machine was before
    units, tests = tmp_path / "failed-units", tmp_path / "bin"
    units.write_text("")
    tests.mkdir()
    for key, value in (("ODD1_DEPLOY_STATE", tmp_path / "state"), ("ODD1_REPO", repo),
                       ("ODD1_DEPLOY_NIXOS_CONFIG", config), ("ODD1_DEPLOY_REBUILD", f"sh {script} {log}"),
                       ("ODD1_DEPLOY_SUDO", ""), ("ODD1_DEPLOY_FAILED_UNITS", f"cat {units}"),
                       ("ODD1_DEPLOY_TESTS_BIN", tests), ("ODD1_DEPLOY_SETTLE", "0"),
                       ("ODD1_LOCAL", tmp_path / "odd1-local"),
                       ("ODD1_DEPLOY_USER_SYSTEMCTL", f"sh {user_systemctl}"), ("FAKE_WANTS", tmp_path / "wants"),
                       ("FAKE_STARTED", tmp_path / "started")):
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv("ODD1_DEPLOY_HOST", raising=False)
    d = Deployer(state=tmp_path / "state", repo=repo, nixos_config=config, rebuild=f"sh {script} {log}", sudo="")
    return {"repo": repo, "d": d, "log": log, "config": config, "first": first, "tmp": tmp_path, "units": units,
            "tests": tests}


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


def fake_test(world, component, code, count=None):
    script = world["tests"] / f"odd1-{component}-test"
    script.write_text(f"#!/bin/sh\n{'echo run >> ' + str(count) if count else ''}\nexit {code}\n")
    script.chmod(0o755)


def test_health_a_changed_components_failing_tests_rolls_back_to_the_previous_release(world):
    repo, d = world["repo"], world["d"]
    d.deploy()
    (repo / "components/widget").mkdir(parents=True)
    commit(repo, "broken", name="components/widget/code.py")
    runs = world["tmp"] / "runs"
    fake_test(world, "widget", 1, runs)
    fake_test(world, "other", 1)  # not changed by this deploy: not run
    with pytest.raises(DeployError, match="rolled back to " + world["first"][:12]):
        d.deploy()
    assert d.current() == world["first"] and len(runs.read_text().splitlines()) == 2  # retried once
    bad, back = d.history()[-2:]
    assert not bad["ok"] and bad["rolled_back_to"] == world["first"] and bad["health"]["tests"] == {"widget": 1}
    assert back["ok"] and back["commit"] == world["first"] and "automatic rollback" in back["reason"]
    assert builds(world)[-1].startswith("start switch v1 ")


def test_health_a_newly_failed_unit_rolls_back_and_one_failing_before_does_not(world):
    repo, d = world["repo"], world["d"]
    world["units"].write_text("old-broken.service loaded failed failed Old\n")
    d.deploy()
    commit(repo, "v2")
    assert d.deploy()["health"]["ok"]  # the unit failing before the deploy is not the deploy's doing
    commit(repo, "v3")
    script = world["tmp"] / "rebuild.sh"
    script.write_text(script.read_text() + f'\necho "new.service loaded failed failed New" >> {world["units"]}\n')
    with pytest.raises(DeployError, match="units failed: new.service"):
        d.deploy()
    assert (d.state / "current").resolve().name == d.history()[-1]["commit"]


def test_health_passing_tests_keep_the_deploy(world):
    repo, d = world["repo"], world["d"]
    d.deploy()
    (repo / "components/widget").mkdir(parents=True)
    second = commit(repo, "fine", name="components/widget/code.py")
    fake_test(world, "widget", 0)
    record = d.deploy()
    assert record["ok"] and record["health"] == {"ok": True, "new_failed_units": [], "tests": {"widget": 0}}
    assert d.current() == second


def test_a_switch_starts_new_user_units_and_leaves_the_others(world):
    repo, d = world["repo"], world["d"]
    commit(repo, "a.service\n", name="wants")
    d.deploy()
    started = world["tmp"] / "started"
    assert started.read_text().split() == ["a.service"]
    started.unlink()  # a.service stopped by the person since: a deploy that does not add it leaves it stopped
    commit(repo, "a.service b.service\n", name="wants")
    assert d.deploy()["user_units"] == {"b.service": 0}
    assert started.read_text().split() == ["b.service"]
    commit(repo, "b.service\n", name="wants")
    started.unlink()
    assert d.deploy(action="build").get("user_units") is None and d.deploy()["user_units"] == {}
    assert not started.exists()


# -- the guard and the person's override ------------------------------------------------------------------------------
from odd1_deploy import guard  # noqa: E402


def test_guard_refuses_activating_the_development_folder_and_lets_releases_through(tmp_path):
    repo, etc = str(tmp_path / "odd1"), tmp_path / "configuration.nix"
    (tmp_path / "odd1" / "nix/hosts").mkdir(parents=True)
    etc.write_text("{ imports = [ /var/lib/odd1/deploy/current/nix/hosts/dev-vm.nix ]; }\n")
    dev = f"nixos-config={repo}/nix/hosts/dev-vm.nix"
    for argv, env in ((["switch", "-I", dev], {}), (["boot", f"-I{dev}"], {}), (["test"], {"NIX_PATH": dev}),
                      (["switch", "--flake", f"{repo}#odd1"], {})):
        assert "refused" in guard.check(argv, env, str(etc), repo), argv
    release = "nixos-config=/var/lib/odd1/deploy/releases/abc/nix/hosts/dev-vm.nix"
    assert guard.check(["switch", "-I", release], {}, str(etc), repo) is None
    assert guard.check(["switch"], {}, str(etc), repo) is None  # the configuration imports the release
    assert guard.check(["build", "-I", dev], {}, str(etc), repo) is None  # building changes nothing
    etc.write_text(f"{{ imports = [ {repo}/nix/hosts/dev-vm.nix ]; }}\n")  # as before odd1-deploy
    assert "it imports from" in guard.check(["switch"], {}, str(etc), repo)
    assert "refused" in guard.check(["switch"], {"ODD1_DEPLOY_ALLOW": "1", "FORCE": "1"}, str(etc), repo)


def test_force_needs_a_person_at_a_terminal_and_the_code_and_is_logged(world):
    repo, d = world["repo"], world["d"]
    d.deploy()
    (repo / "app.txt").write_text("hotfix, not committed")
    d.force_rebuild = d.rebuild
    with pytest.raises(DeployError, match="person at a terminal"):  # an agent's shell: no terminal
        d.force("hotfix", ask=lambda _: "", say=lambda _: None, at_terminal=lambda: False)
    shown = []
    with pytest.raises(DeployError, match="not confirmed"):
        d.force("hotfix", ask=lambda _: "0000x", say=shown.append, at_terminal=lambda: True)
    assert any("app.txt" in line for line in shown) and len(builds(world)) == 1

    def type_the_code(prompt):
        return prompt.split("Type ")[1].split(" ")[0]
    record = d.force("hotfix for the clock", ask=type_the_code, say=lambda _: None, at_terminal=lambda: True)
    assert record["forced"] and record["dirty"] == [" M app.txt"] and record["reason"] == "hotfix for the clock"
    assert builds(world)[-1].startswith("start switch hotfix, not committed")
    assert d.history()[-1]["forced"] and d.status()["forced"]["reason"] == "hotfix for the clock"
    git(repo, "checkout", "app.txt")
    commit(repo, "v2")
    d.deploy()  # the next normal deploy returns to a release
    assert d.status()["forced"] is None


# -- the install's local settings ---------------------------------------------------------------------------------------
def make_local(world, text="{ odd1.person.name = \"Ada\"; }"):
    local = world["tmp"] / "odd1-local"
    local.mkdir(exist_ok=True)
    if not (local / ".git").exists():
        git(world["tmp"], "init", "-q", "-b", "main", str(local))
        git(local, "config", "user.email", "t@t")
        git(local, "config", "user.name", "t")
    (local / "host.nix").write_text(text + "\n")
    git(local, "add", "host.nix")
    git(local, "commit", "-qm", "settings")
    return local, git(local, "rev-parse", "HEAD")


def test_local_config_is_built_with_the_release_and_the_system_imports_both(world):
    d = world["d"]
    d.deploy()  # first without local settings: the host file alone
    local, lsha = make_local(world)
    (local / "host.nix").write_text("{ uncommitted = true; }\n")  # only committed local settings ship
    record = d.deploy()
    assert record["local"] == lsha and record["release"] == f"{world['first']}-{lsha[:12]}"
    release = d.state / "releases" / record["release"]
    assert (release / "local" / "host.nix").read_text() == '{ odd1.person.name = "Ada"; }\n'
    assert "nix/hosts/test.nix ./local/host.nix" in (release / "odd1-config.nix").read_text()
    assert builds(world)[-1].endswith('local:{ odd1.person.name = "Ada"; }')
    assert f"{d.state}/current/odd1-config.nix" in world["config"].read_text()
    assert d.status()["local"] == lsha and d.current() == world["first"]


def test_local_config_change_deploys_and_rolls_back_with_its_release(world):
    d = world["d"]
    local, first_local = make_local(world)
    d.deploy()
    _, second_local = make_local(world, "{ odd1.person.name = \"Grace\"; }")
    assert d.deploy()["local"] == second_local  # same public commit, new local settings: a new release
    back = d.rollback()
    assert back["local"] == first_local and d.current_local() == first_local
    assert builds(world)[-1].endswith('local:{ odd1.person.name = "Ada"; }')


def test_local_config_for_a_checkout_joins_it_with_the_local_settings(world):
    d = world["d"]
    local, _ = make_local(world)
    cfg = d.config_for(world["repo"])
    assert f"{world['repo']}/nix/hosts/test.nix {local}/host.nix" in cfg.read_text()


def test_a_pinned_nixpkgs_is_built_with_and_a_tree_without_a_pin_uses_the_default(world):
    repo, d = world["repo"], world["d"]
    assert d.nixpkgs(repo) is None and d.nixpkgs_args(repo) == []  # no nix/nixpkgs.json: the default nixpkgs
    fake = world["tmp"] / "instantiate.sh"
    fake.write_text('echo "$@" > "$(dirname "$0")/instantiate.args"; echo \'"/nix/store/pinned-source"\'\n')
    d.instantiate = ["sh", str(fake)]
    d.rebuild = ["sh", "-c", 'echo "$@" > "$0.args"', str(world["tmp"] / "rebuild")]
    (repo / "nix/nixpkgs.json").write_text(json.dumps({"version": "25.05.x", "url": "https://example/n.tar.gz",
                                                       "sha256": "0abc"}))
    git(repo, "add", ".")
    commit(repo, "pinned")
    d.deploy()
    assert (world["tmp"] / "rebuild.args").read_text().split()[-2:] == ["-I", "nixpkgs=/nix/store/pinned-source"]
    expr = (world["tmp"] / "instantiate.args").read_text()
    assert 'url = "https://example/n.tar.gz"' in expr and 'sha256 = "0abc"' in expr
    assert d.nixpkgs(repo) == "/nix/store/pinned-source"
