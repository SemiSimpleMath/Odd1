"""The wiki's search over the repository's own wiki/ and a made-up one: active rules first, superseded ones last."""
from pathlib import Path

from odd1_wiki import cli, wiki

REPO_WIKI = Path(__file__).resolve().parents[3] / "wiki"


def make(tmp_path):
    (tmp_path / "decisions").mkdir()
    (tmp_path / "index.md").write_text("# Wiki\nAbout deploys and more.\n")
    (tmp_path / "guide.md").write_text("# Guide\n## Deploys\nRun odd1-deploy to deploy.\n")
    (tmp_path / "decisions" / "0001-old.md").write_text(
        "---\ntitle: Old deploy rule\nstatus: superseded\nsuperseded_by: 0002\n---\n# Old\nDeploy from the folder.\n")
    (tmp_path / "decisions" / "0002-new.md").write_text(
        "---\ntitle: Deploy rule\nstatus: active\nticket: #33\n---\n# New\nDeploy only committed work.\n")
    return tmp_path


def test_search_puts_active_rules_first_and_superseded_last(tmp_path):
    hits = wiki.search("deploy", make(tmp_path))
    assert hits[0]["page"] == "decisions/0002-new.md" and hits[0]["status"] == "active"
    assert hits[-1]["page"] == "decisions/0001-old.md" and hits[-1]["superseded_by"] == "0002"
    assert "SUPERSEDED by 0002" in wiki.format_hit(hits[-1])
    assert wiki.search("deploy guide", tmp_path)[0]["section"] == "Deploys"


def test_show_reads_pages_and_refuses_paths_outside(tmp_path):
    base = make(tmp_path)
    assert "odd1-deploy" in wiki.show("guide.md", base)
    for bad in ("../etc/passwd", "missing.md"):
        try:
            wiki.show(bad, base)
            raise AssertionError(bad)
        except FileNotFoundError:
            pass


def test_the_repository_wiki_holds_the_persons_rules(monkeypatch, capsys):
    if not REPO_WIKI.exists():  # installed, the deployed wiki is read instead
        REPO = Path("/var/lib/odd1/deploy/current/wiki")
    else:
        REPO = REPO_WIKI
    monkeypatch.setattr(wiki, "CANDIDATES", [str(REPO)])
    rules = [p for p in wiki.pages() if p["decision"] and p["status"] == "active"]
    assert {"0002-the-persons-wishes-come-first.md", "0003-the-persons-data-is-never-destroyed.md"} <= {
        Path(p["page"]).name for p in rules}
    assert cli.main(["search", "data", "backups"]) == 0
    assert capsys.readouterr().out.startswith("[active rule] decisions/0003")
