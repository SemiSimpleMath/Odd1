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


def run_with(repo, tmp_path, person=None, secret=None):
    import json
    import os
    import sqlite3
    env = dict(os.environ, HOME=str(tmp_path / "home"))
    if person:
        (tmp_path / "person.json").write_text(json.dumps(person))
        env["ODD1_CHECK_PERSON"] = str(tmp_path / "person.json")
    else:
        env["ODD1_CHECK_PERSON"] = str(tmp_path / "none.json")
    vault = tmp_path / "vault.db"
    vault.unlink(missing_ok=True)
    db = sqlite3.connect(vault)
    db.execute("CREATE TABLE values_(uri TEXT, value TEXT)")
    if secret:
        db.execute("INSERT INTO values_ VALUES ('pod:secret.token:abcdef12', ?)", (secret,))
    db.commit()
    env["ODD1_CHECK_SECRETS"] = str(vault)
    return subprocess.run([sys.executable, str(repo / "tools" / "check.py")], capture_output=True, text=True, env=env)


def test_private_data_the_persons_details_and_stored_secrets_stay_out(tmp_path):
    repo = make_repo(tmp_path)
    person = {"name": "Ada Lovelace", "email": "ada@example.org"}
    assert run_with(repo, tmp_path, person).returncode == 0
    note = repo / "components" / "thing" / "notes.txt"
    note.write_text("ask Ada Lovelace, ada@example.org\n")
    out = run_with(repo, tmp_path, person)
    assert out.returncode == 1 and "the person's name" in out.stdout and "the person's email" in out.stdout
    assert "Ada Lovelace" not in out.stdout and "/srv/odd1-local" in out.stdout
    note.write_text("key: tok-very-secret-value\n")
    out = run_with(repo, tmp_path, None, "tok-very-secret-value")
    assert out.returncode == 1 and "a stored secret (pod:secret.token:abcdef12)" in out.stdout
    assert "tok-very-secret-value" not in out.stdout


def test_private_data_keys_tokens_and_addresses_outside_host_files(tmp_path):
    repo = make_repo(tmp_path)
    thing = repo / "components" / "thing"
    (thing / "id").write_text("-----BEGIN OPENSSH " + "PRIVATE KEY-----\n")
    address = ".".join(["10", "0", "2", "100"])  # spelled out so this file does not trip the rule itself
    (thing / "conf.py").write_text(f"PROXY = 'http://{address}:3128'\nKEY = 'sk-" + "a" * 30 + "'\n")
    (repo / "nix" / "hosts").mkdir(parents=True)
    (repo / "nix" / "hosts" / "vm.nix").write_text(f'networking.proxy.default = "http://{address}:3128";\n')
    out = run_with(repo, tmp_path)
    assert "id: holds a private key" in out.stdout and "a private network address" in out.stdout
    assert "an API token" in out.stdout and "vm.nix" not in out.stdout
