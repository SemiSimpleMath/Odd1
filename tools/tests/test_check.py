"""The fitness check looks at what the repository holds or would take in a commit, not at ignored leftovers."""
import subprocess
import sys
from pathlib import Path

CHECK = Path(__file__).resolve().parents[1] / "check.py"


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / "check.py").write_text(CHECK.read_text())
    c = repo / "components" / "thing"
    (c / "tests").mkdir(parents=True)
    (c / "README.md").write_text("# thing\nDoes one thing.\n")
    (c / "tests" / "test_x.py").write_text("def test_x():\n    pass\n")
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    return repo


def run(repo):
    return subprocess.run([sys.executable, str(repo / "tools" / "check.py")], capture_output=True, text=True)


def test_check_ignores_untracked_caches(tmp_path):
    repo = make_repo(tmp_path)
    cache = repo / "components" / "thing" / "tests" / "__pycache__"
    cache.mkdir()
    (cache / "test_x.cpython-312.pyc").write_bytes(b"\0")
    (repo / "components" / "thing" / ".pytest_cache").mkdir()
    assert run(repo).returncode == 0


def test_check_still_refuses_a_cache_git_would_take(tmp_path):
    repo = make_repo(tmp_path)
    (repo / ".gitignore").write_text("")
    (repo / "components" / "thing" / "stray.pyc").write_bytes(b"\0")
    out = run(repo)
    assert out.returncode == 1 and "stray.pyc" in out.stdout


def test_check_refuses_a_direct_nixos_rebuild_outside_deploy(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "components" / "thing" / "go.sh").write_text("sudo nixos-rebuild " + "switch\n")
    out = run(repo)
    assert out.returncode == 1 and "odd1-deploy" in out.stdout
