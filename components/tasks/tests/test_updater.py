"""The updater's pin helper: a Python package pinned in nix/python-updates.json from PyPI's own release data, the
components that use it named, and major version jumps refused (they go to a person) unless --major says a person
approved one."""
import importlib.util
import io
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROLES = Path(__file__).resolve().parents[1] / "roles"
PIN = ROLES / "updater" / "pin-python.py"
CHECK_VERSIONS = ROLES / "update-watcher" / "check-versions.py"
SHA = "ab" * 32


@pytest.fixture
def worktree(tmp_path, monkeypatch):
    (tmp_path / "nix").mkdir()
    (tmp_path / "nix" / "python-updates.json").write_text("{}\n")
    for name, packages in (("browser", "p.selenium p.jsonschema p.pytest"), ("screen", "p.pytest p.pillow")):
        (tmp_path / "components" / name).mkdir(parents=True)
        (tmp_path / "components" / name / "module.nix").write_text(
            f"python = pkgs.odd1Python.withPackages (p: [ {packages} ]);\n")
    known = tmp_path / "python-packages.json"
    known.write_text(json.dumps([{"attr": "selenium", "name": "selenium", "version": "4.29.0"},
                                 {"attr": "pillow", "name": "pillow", "version": "11.2.1"},
                                 {"attr": "pygobject3", "name": "pygobject", "version": "3.50.0"}]))
    monkeypatch.setenv("ODD1_TASKS_PYPKGS", str(known))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(monkeypatch, capsys, *args, release=None):
    asked = []

    def urlopen(url, timeout=None):
        asked.append(url)
        return io.BytesIO(json.dumps(release).encode())
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr(sys, "argv", ["pin-python.py", *args])
    with pytest.raises(SystemExit) as code:
        runpy.run_path(str(PIN), run_name="__main__")
    return code.value.code, capsys.readouterr().out, asked


def test_a_minor_update_is_pinned_from_pypis_data(worktree, monkeypatch, capsys):
    release = {"urls": [{"packagetype": "bdist_wheel", "url": "w", "digests": {"sha256": "00" * 32}},
                        {"packagetype": "sdist", "url": "https://files/selenium-4.51.0.tar.gz",
                         "digests": {"sha256": SHA}}]}
    code, out, asked = run(monkeypatch, capsys, "selenium", "4.51.0", release=release)
    assert code == 0 and asked == ["https://pypi.org/pypi/selenium/4.51.0/json"]
    pins = json.loads((worktree / "nix" / "python-updates.json").read_text())
    assert pins["selenium"]["url"].endswith("selenium-4.51.0.tar.gz") and pins["selenium"]["version"] == "4.51.0"
    assert pins["selenium"]["hash"] == "sha256-q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s="
    assert json.loads(out)["components"] == ["browser"]


def test_pypi_names_find_their_nix_attribute(worktree, monkeypatch, capsys):
    release = {"urls": [{"packagetype": "sdist", "url": "https://files/pygobject-3.58.1.tar.gz",
                         "digests": {"sha256": SHA}}]}
    code, out, _ = run(monkeypatch, capsys, "PyGObject", "3.58.1", release=release)
    assert code == 0 and json.loads(out)["pinned"] == "pygobject3"


def test_a_major_update_is_refused_and_nothing_changes(worktree, monkeypatch, capsys):
    code, out, asked = run(monkeypatch, capsys, "pillow", "12.3.0")
    assert code == 4 and "major update" in out and asked == []
    assert (worktree / "nix" / "python-updates.json").read_text() == "{}\n"


def test_a_major_update_a_person_approved_is_pinned_with_major(worktree, monkeypatch, capsys):
    release = {"urls": [{"packagetype": "sdist", "url": "https://files/pillow-12.3.0.tar.gz",
                         "digests": {"sha256": SHA}}]}
    code, out, _ = run(monkeypatch, capsys, "pillow", "12.3.0", "--major", release=release)
    assert code == 0 and json.loads(out)["to"] == "12.3.0"
    assert json.loads((worktree / "nix" / "python-updates.json").read_text())["pillow"]["version"] == "12.3.0"


def watcher_rows(monkeypatch, capsys, available):
    spec = importlib.util.spec_from_file_location("check_versions", CHECK_VERSIONS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "run", lambda *cmd: "2.1.0 (Claude Code)" if cmd[0] == "claude" else "2.1.5")
    monkeypatch.setattr(module, "pypi", lambda name: available)
    monkeypatch.setattr(module, "dist", lambda p: ("PyGObject", p["version"]) if p["attr"] == "pygobject3" else None)
    module.main()
    return {r["name"]: r for r in json.loads(capsys.readouterr().out)}


def test_python_update_check_reads_odd1s_own_python_by_attribute(worktree, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ODD1_TASKS_STATE", str(tmp_path / "state"))
    rows = watcher_rows(monkeypatch, capsys, "3.58.1")
    check = rows["PyGObject"]["done_when"]
    assert check == f"python3 {tmp_path}/state/roles/updater/python-installed.py pygobject3 3.58.1"
    assert "importlib" not in check
    assert rows["@anthropic-ai/claude-code"]["done_when"] == "claude --version | grep -q '2.1.5'"
    assert "done_when" not in rows["selenium"]  # no update, no task
    # The check fails before the pin is deployed and passes once python-packages.json has the new version.
    (tmp_path / "state" / "roles").mkdir(parents=True)
    (tmp_path / "state" / "roles" / "updater").symlink_to(ROLES / "updater")
    command = check.replace("python3", sys.executable, 1)
    assert subprocess.run(command, shell=True, capture_output=True).returncode == 1
    known = worktree / "python-packages.json"
    packages = json.loads(known.read_text())
    next(p for p in packages if p["attr"] == "pygobject3")["version"] = "3.58.1"
    known.write_text(json.dumps(packages))
    assert subprocess.run(command, shell=True, capture_output=True).returncode == 0


def test_python_update_check_is_what_the_watcher_is_told_to_copy():
    text = (ROLES / "update-watcher" / "CLAUDE.md").read_text()
    assert "check-versions.py printed" in text and "python3 -c" not in text


def test_updater_may_apply_a_python_update_and_nothing_broader():
    perms = json.loads((ROLES / "updater" / "settings.json").read_text())["permissions"]
    bash = {a for a in perms["allow"] if a.startswith("Bash")}
    applying = {"Bash(python3 /var/lib/odd1/tasks/roles/updater/pin-python.py:*)",
                "Bash(python3 /var/lib/odd1/tasks/roles/updater/python-installed.py:*)",
                "Bash(git add:*)", "Bash(git commit:*)", "Bash(odd1-tasks-check:*)", "Bash(odd1-tasks-land:*)",
                "Bash(claude --version)", "Bash(npm install -g @anthropic-ai/claude-code@*)"}
    looking = {"Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)",
               "Bash(git restore:*)", "Bash(npm view:*)", "Bash(ls:*)", "Bash(cat:*)", "Bash(grep:*)",
               "Bash(journalctl:*)", "Bash(odd1-tasks list:*)", "Bash(odd1-tasks show:*)"}
    assert bash == applying | looking
    assert {"Bash(python3 -c:*)", "Bash(git push:*)", "Bash(git rebase:*)", "Bash(odd1-deploy:*)",
            "Bash(sudo:*)"} <= set(perms["deny"])
    assert [a for a in perms["allow"] if a.startswith(("Edit", "Write"))] == ["Edit(./nix/python-updates.json)"]


def test_updater_may_apply_what_its_instructions_name():
    text = (ROLES / "updater" / "CLAUDE.md").read_text()
    for step in ("pin-python.py", "git add nix/python-updates.json && git commit", "odd1-tasks-check",
                 "odd1-tasks-land", "python-installed.py"):
        assert step in text
