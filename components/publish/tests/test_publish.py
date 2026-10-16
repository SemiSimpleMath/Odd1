import subprocess

import pytest

from odd1_publish import cli, rules

# Built here so this file holds no credential shape (tools/check.py refuses those in the repository).
FAKE = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
TEST_KEY = "sk-" + "test-0123456789abcdef"
PRIVATE = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"


def test_only_plain_github_urls():
    assert rules.remote_ok("https://github.com/SemiSimpleMath/Odd1.git")
    assert rules.remote_ok("https://github.com/SemiSimpleMath/Odd1")
    assert not rules.remote_ok("https://x-access-token:abc@github.com/a/b.git")
    assert not rules.remote_ok("https://gitlab.com/a/b.git")
    assert not rules.remote_ok("git@github.com:a/b.git")
    assert not rules.remote_ok("")


def test_credentials_in_added_lines_are_caught_and_masked():
    diff = f"+++ b/x.py\n+TOKEN = '{FAKE}'\n-OLD = '{FAKE}'\n+TEST = '{TEST_KEY}'\n"
    found = rules.leaks(diff)
    assert len(found) == 1 and FAKE not in found[0] and "masked" in found[0]
    assert rules.leaks("+" + PRIVATE)


def test_a_push_never_forces():
    assert rules.plan(None, "b", False) == "create"
    assert rules.plan("b", "b", False) == "up-to-date"
    assert rules.plan("a", "b", True) == "fast-forward"
    assert rules.plan("a", "b", False) == "diverged"


def sh(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """A repository with a local bare remote; the vault and GitHub checks are stood in for."""
    bare, work = tmp_path / "remote.git", tmp_path / "work"
    sh("git", "init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    sh("git", "init", "-q", "-b", "main", str(work), cwd=tmp_path)
    for k, v in (("user.name", "t"), ("user.email", "t@example.org")):
        sh("git", "config", k, v, cwd=work)
    (work / "a.txt").write_text("one\n")
    sh("git", "add", ".", cwd=work)
    sh("git", "commit", "-qm", "one", cwd=work)
    sh("git", "remote", "add", "origin", str(bare), cwd=work)
    monkeypatch.setattr(cli, "REPO", str(work))
    monkeypatch.setattr(rules, "remote_ok", lambda url: True)
    monkeypatch.setattr(cli, "token_pod", lambda given=None: "pod:secret.token:test")
    monkeypatch.setattr(cli, "with_token", lambda pod, *a: subprocess.run(["git", "-C", str(work), *a],
                                                                          capture_output=True, text=True))
    return work, bare


def head(repo):
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", "main"], capture_output=True, text=True)
    return r.stdout


def test_pushes_and_then_is_up_to_date(repos, capsys):
    work, bare = repos
    assert cli.publish(dry_run=True) == 0 and head(bare) == ""
    assert cli.publish() == 0 and head(bare) == head(work)
    assert cli.publish() == 0
    assert "up-to-date" in capsys.readouterr().out


def test_a_commit_with_a_credential_is_not_pushed(repos):
    work, bare = repos
    cli.publish()
    (work / "b.py").write_text(f"KEY = '{FAKE}'\n")
    sh("git", "add", ".", cwd=work)
    sh("git", "commit", "-qm", "key", cwd=work)
    with pytest.raises(cli.PublishError, match="credentials"):
        cli.publish()
    assert head(bare) != head(work)


def test_a_remote_that_moved_on_is_refused(repos, tmp_path):
    work, bare = repos
    cli.publish()
    other = tmp_path / "other"
    sh("git", "clone", "-q", str(bare), str(other), cwd=tmp_path)
    for k, v in (("user.name", "o"), ("user.email", "o@example.org")):
        sh("git", "config", k, v, cwd=other)
    (other / "c.txt").write_text("elsewhere\n")
    sh("git", "add", ".", cwd=other)
    sh("git", "commit", "-qm", "elsewhere", cwd=other)
    sh("git", "push", "-q", "origin", "main", cwd=other)
    (work / "d.txt").write_text("here\n")
    sh("git", "add", ".", cwd=work)
    sh("git", "commit", "-qm", "here", cwd=work)
    with pytest.raises(cli.PublishError, match="never forces"):
        cli.publish()
