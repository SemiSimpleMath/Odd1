import json

from odd1_person import cli, profile


def setup(tmp_path, installed=None, own=None):
    system, user = tmp_path / "system.json", tmp_path / "home" / "person.json"
    system.write_text(json.dumps(installed or {}))
    if own is not None:
        user.parent.mkdir()
        user.write_text(json.dumps(own))
    return str(system), str(user)


def test_own_settings_win_over_installed_ones(tmp_path):
    system, user = setup(tmp_path, {"name": "Ada Lovelace", "email": "ada@example.org"}, {"name": "Ada King"})
    current = profile.load(system, user)
    assert current["name"] == "Ada King" and current["email"] == "ada@example.org" and current["pronouns"] == ""


def test_save_keeps_only_what_differs_and_survives_a_broken_file(tmp_path):
    system, user = setup(tmp_path, {"name": "Ada Lovelace"})
    profile.save({"name": "Ada Lovelace", "call_me": " Ada ", "bogus": "x"}, system, user)
    assert json.loads(open(user).read()) == {"call_me": "Ada"}
    open(user, "w").write("{not json")
    assert profile.load(system, user)["name"] == "Ada Lovelace"


def test_context_names_the_person_and_defaults_to_they(tmp_path):
    text = profile.context({"name": "Ada Lovelace", "call_me": "", "pronouns": "", "language": "", "email": "",
                            "about": "Mathematician.\n Likes short answers."})
    assert "Ada Lovelace. Call them Ada." in text and "use they/them" in text
    assert "In their words: Mathematician. Likes short answers." in text
    assert "has not told" in profile.context({k: "" for k in profile.KEYS})
    assert "Pronouns: she/her." in profile.context({**dict.fromkeys(profile.KEYS, ""), "name": "Ada",
                                                     "pronouns": "she/her"})


def test_git_identity_follows_the_name():
    calls = []
    profile.sync_git({"name": "Ada", "email": ""}, run=lambda args, check: calls.append(args))
    assert calls == [["git", "config", "--global", "user.name", "Ada"]]


def test_cli_get_and_set(tmp_path, monkeypatch, capsys):
    system, user = setup(tmp_path, {"name": "Ada Lovelace"})
    monkeypatch.setattr(profile, "SYSTEM", system)
    monkeypatch.setenv("ODD1_PERSON", user)
    cli.main(["get", "call_me"])
    assert capsys.readouterr().out == "Ada\n"
    cli.main(["set", "pronouns", "she/her"])
    cli.main(["context"])
    assert "Pronouns: she/her." in capsys.readouterr().out
