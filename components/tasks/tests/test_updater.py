"""The updater's pin helper: a Python package pinned in nix/python-updates.json from PyPI's own release data, the
components that use it named, and major version jumps refused (they go to a person) unless --major says a person
approved one."""
import io
import json
import runpy
import sys
from pathlib import Path

import pytest

PIN = Path(__file__).resolve().parents[1] / "roles" / "updater" / "pin-python.py"
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
