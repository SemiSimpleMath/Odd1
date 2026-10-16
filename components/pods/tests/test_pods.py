"""Pods and secrets: references instead of content, authority levels, secrets agents can hand on but never read,
the courier putting them only where allowed and scrubbing what comes back, and an audit without values."""
import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from odd1_pods import cli, courier
from odd1_pods.store import PodError, Pods

TOKEN = "sk-test-0123456789abcdef"


@pytest.fixture
def pods(tmp_path, monkeypatch):
    monkeypatch.delenv("ODD1_TASKS_AGENT", raising=False)
    monkeypatch.setenv("USER", "jukka")
    return Pods(tmp_path / "pods", tmp_path / "secrets")


@pytest.fixture
def as_agent(monkeypatch):
    monkeypatch.setenv("ODD1_TASKS_AGENT", "worker")


def test_content_by_reference_one_pod_per_content_and_registered_kinds_only(pods, tmp_path):
    uri = pods.put("note", "the deploy guard refuses /srv/odd1", tags=["deploy"])
    assert uri.startswith("pod:note:") and pods.put("note", "the deploy guard refuses /srv/odd1") == uri
    assert pods.get(uri) == "the deploy guard refuses /srv/odd1"
    picture = tmp_path / "shot.png"
    picture.write_bytes(b"\x89PNG fake")
    shot = pods.put("image", "a screenshot of the taskbar", file=picture, sources=[uri])
    assert pods.file(shot).read_bytes() == b"\x89PNG fake" and pods.header(shot)["sources"] == [uri]
    assert [h["uri"] for h in pods.find(tag="deploy")] == [uri]
    with pytest.raises(PodError, match="unknown kind"):
        pods.put("spaceship", "x")


def test_secrets_agents_hand_on_but_never_read(pods, as_agent, monkeypatch):
    with pytest.raises(PodError, match="only a person"):
        pods.put_secret("secret.token", TOKEN, name="OPENAI_API_KEY")
    monkeypatch.delenv("ODD1_TASKS_AGENT")
    uri = pods.put_secret("secret.token", TOKEN, name="OPENAI_API_KEY", allow=["host:api.openai.com"])
    monkeypatch.setenv("ODD1_TASKS_AGENT", "worker")
    assert pods.get(uri, "last4") == "cdef" and pods.get(uri, "length") == str(len(TOKEN))
    for projection in ("body", "full"):
        with pytest.raises(PodError, match="no such pod, or not at your authority"):
            pods.get(uri, projection, authority=99)  # not even a person's level reads it
    with pytest.raises(PodError, match="no such pod, or not at your authority"):  # same answer as a missing pod
        pods.get("pod:secret.token:doesnotexist")
    with pytest.raises(PodError, match="secret kind"):
        pods.put("secret.token", TOKEN)
    assert TOKEN.encode() not in (pods.state / "pods.db").read_bytes()  # the value lives only in the vault
    assert oct(os.stat(pods.secrets_dir / "pods.db").st_mode & 0o777) == "0o600"
    audit = json.dumps(pods.audit())
    assert TOKEN not in audit and '"allowed": 0' in audit


def test_the_courier_puts_a_secret_only_where_its_pod_allows_and_scrubs_the_reply(pods):
    uri = pods.put_secret("secret.token", TOKEN, name="API", allow=["host:127.0.0.1"])
    other = pods.put_secret("secret.token", "another-secret-value", name="OTHER", allow=["host:api.other.com"])
    seen = {}

    class Echo(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["auth"] = self.headers["Authorization"]
            reply = json.dumps({"you_sent": self.headers["Authorization"],
                                "b64": base64.b64encode(TOKEN.encode()).decode()}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *a):
            pass
    server = HTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=server.handle_request, daemon=True).start()
    out = courier.http(pods, "POST", f"http://127.0.0.1:{server.server_port}/v1",
                       {"Authorization": f"Bearer {{{{{uri}}}}}"}, "{}")
    assert seen["auth"] == f"Bearer {TOKEN}"  # the service got the real value
    assert out["ok"] and TOKEN not in json.dumps(out) and f"[{uri}]" in out["body"]
    assert base64.b64encode(TOKEN.encode()).decode().rstrip("=") not in out["body"]
    with pytest.raises(PodError, match="may not go to host:127.0.0.1"):
        courier.http(pods, "POST", f"http://127.0.0.1:{server.server_port}/", {"X": f"{{{{{other}}}}}"})
    with pytest.raises(PodError, match="https only"):
        courier.http(pods, "GET", "http://example.com/", {"X": f"{{{{{uri}}}}}"})
    with pytest.raises(PodError, match="not the URL"):
        courier.http(pods, "GET", f"https://127.0.0.1/?k={{{{{uri}}}}}")


def test_the_courier_runs_a_program_with_the_secret_in_its_environment_and_scrubs_its_output(pods):
    uri = pods.put_secret("secret.token", TOKEN, name="GH", allow=["env:GH_TOKEN"])
    out = courier.run(pods, ["sh", "-c", 'echo "token is $GH_TOKEN"; test -n "$GH_TOKEN"'], {"GH_TOKEN": uri})
    assert out["exit"] == 0 and out["stdout"] == f"token is [{uri}]\n"
    with pytest.raises(PodError, match="may not go to env:OTHER"):
        courier.run(pods, ["true"], {"OTHER": uri})


def test_a_person_changes_where_a_secret_may_go_and_agents_cannot(pods, as_agent, monkeypatch):
    monkeypatch.delenv("ODD1_TASKS_AGENT")
    uri = pods.put_secret("secret.card", "4111111111111111", name="card")
    monkeypatch.setenv("ODD1_TASKS_AGENT", "worker")
    with pytest.raises(PodError, match="only a person"):
        pods.allow(uri, ["host:shop.example"])
    monkeypatch.delenv("ODD1_TASKS_AGENT")
    assert pods.allow(uri, ["field:card-number"])["allow"] == ["field:card-number"]


def test_secrets_cli_needs_a_person_at_a_terminal_and_reads_env_files(pods, capsys, monkeypatch):
    monkeypatch.setattr(cli, "Pods", lambda: pods)
    assert cli.main(["set", "X"], which="secrets") == 1
    assert "at a terminal" in capsys.readouterr().err
    assert cli.parse_env("# keys\nexport A=1\nB='two words'\nC=\"x=y\"  \nD=plain # note\n\nbad line\n") == {
        "A": "1", "B": "two words", "C": "x=y", "D": "plain"}
    pods.put_secret("secret.token", TOKEN, name="OPENAI_API_KEY", allow=["env:OPENAI_API_KEY"])
    assert cli.main(["list"], which="secrets") == 0
    listed = capsys.readouterr().out
    assert "OPENAI_API_KEY" in listed and "…cdef" in listed and TOKEN not in listed
