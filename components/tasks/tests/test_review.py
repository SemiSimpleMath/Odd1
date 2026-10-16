"""The reviewer: another agent reads a branch before it lands, and its verdict decides (approve, changes, escalate)."""
import json
import subprocess

import pytest
from conftest import PERSON, approved

from odd1_tasks import review, roles


@pytest.fixture
def branch(store, tmp_path):
    roles.seed(store, "reviewer")
    wt = tmp_path / "wt"
    wt.mkdir()
    for args in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                                                "--allow-empty", "-m", "start"], ["checkout", "-q", "-b", "task/1"]):
        subprocess.run(["git", *args], cwd=wt, check=True)
    (wt / "fix.py").write_text("print('fixed')\n")
    subprocess.run(["git", "add", "fix.py"], cwd=wt, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "fix"], cwd=wt, check=True)
    t = approved(store, "Fix it", done_when="true")
    store.claim("housekeeper", t["id"])
    return wt, t


def plan(monkeypatch, verdict, notes):
    line = json.dumps({"verdict": verdict, "notes": notes})
    monkeypatch.setenv("STUB_PLAN", json.dumps({"result": f"Looked at fix.py.\n{line}"}))


def test_reviewer_reads_the_diff_and_its_approval_lands(store, branch, monkeypatch, tmp_path):
    wt, t = branch
    plan(monkeypatch, "approve", "small, tested")
    assert review.review(store, t["id"], str(wt), "check passed")["verdict"] == "approve"
    call = [json.loads(line) for line in open(tmp_path / "stub.jsonl")][-1]
    assert "print('fixed')" in call["prompt"] and "check passed" in call["prompt"] and call["agent"] == "reviewer"
    assert call["cwd"] == str(wt) and str(roles.folder(store, "reviewer") / "settings.json") in call["argv"]
    entry = store.show(t["id"])["log"][-1]
    assert entry["kind"] == "review" and entry["actor"] == "reviewer" and entry["text"] == "approve: small, tested"


def test_reviewer_changes_and_escalations_come_back_with_their_notes(store, branch, monkeypatch, capsys):
    wt, t = branch
    monkeypatch.setenv("ODD1_TASKS_WORKTREE", str(wt))
    plan(monkeypatch, "changes", "add a test for the empty case")
    assert review.main([str(t["id"])]) == 6 and "add a test for the empty case" in capsys.readouterr().out
    plan(monkeypatch, "escalate", "this deletes the person's files")
    assert review.main([str(t["id"])]) == 5
    monkeypatch.setenv("STUB_PLAN", json.dumps({"result": "I think it is fine."}))  # no verdict: a person looks
    assert review.main([str(t["id"])]) == 5


def test_reviewer_permissions_are_read_only(store):
    roles.seed(store, "reviewer")
    perms = json.loads((roles.folder(store, "reviewer") / "settings.json").read_text())["permissions"]
    assert {"Edit", "Write", "Bash(git commit:*)", "Bash(git push:*)"} <= set(perms["deny"])
    assert not any(a in ("Bash", "Edit", "Write") or a.startswith(("Edit", "Write")) for a in perms["allow"])
