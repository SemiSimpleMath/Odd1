"""The agent systems registry as the shell reads it: Claude first, whatever the file holds; a missing file still
offers Claude."""
import json

from odd1_desktop import shell


def test_registry_puts_claude_first_and_keeps_every_system(tmp_path, monkeypatch):
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"tasks": {"title": "Task manager", "command": "odd1-tasks console"},
                                "claude": {"title": "Claude", "command": "claude"}}))
    monkeypatch.setenv("ODD1_AGENTS", str(path))
    monkeypatch.delenv("ODD1_CHAT_COMMAND", raising=False)
    agents = shell.load_agents()
    assert list(agents) == ["claude", "tasks"] and agents["tasks"]["command"] == "odd1-tasks console"


def test_no_registry_still_offers_claude_and_chat_command_overrides_it(tmp_path, monkeypatch):
    monkeypatch.setenv("ODD1_AGENTS", str(tmp_path / "missing.json"))
    monkeypatch.setenv("ODD1_CHAT_COMMAND", "aider")
    assert shell.load_agents() == {"claude": {"title": "Claude", "description": "A new conversation with Claude Code",
                                              "command": "aider"}}


def test_open_chat_tags_the_terminal_with_its_agent_system(tmp_path, monkeypatch):
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"tasks": {"title": "Task manager", "command": "odd1-tasks console"}}))
    monkeypatch.setenv("ODD1_AGENTS", str(path))
    started = []
    monkeypatch.setattr(shell.subprocess, "Popen", lambda args, **kw: started.append(args))
    monkeypatch.setattr(shell.shutil, "which", lambda name: f"/bin/{name}")
    shell.open_chat(agent="tasks")
    args = started[0][started[0].index("odd1-desktop"):]
    assert args[1] == "chat" and "--role=odd1-agent:tasks" in args and "--title=Task manager" in args
    assert args[-1] == "--command=odd1-tasks console"


def test_without_the_chat_window_a_chat_is_a_plain_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("ODD1_AGENTS", str(tmp_path / "missing.json"))
    monkeypatch.delenv("ODD1_CHAT_COMMAND", raising=False)
    started = []
    monkeypatch.setattr(shell.subprocess, "Popen", lambda args, **kw: started.append(args))
    monkeypatch.setattr(shell.shutil, "which", lambda name: None)
    shell.open_chat()
    args = started[0]
    assert args[0] == "xfce4-terminal" and "--role=odd1-agent:claude" in args and args[-1] == "claude; exec bash"


def test_a_window_agent_opens_its_own_window_with_its_role_and_no_terminal(tmp_path, monkeypatch):
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"chat": {"title": "Chat", "command": "odd1-chat", "window": True}}))
    monkeypatch.setenv("ODD1_AGENTS", str(path))
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    started = []
    monkeypatch.setattr(shell.subprocess, "Popen", lambda args, **kw: started.append((args, kw)))
    shell.open_chat(agent="chat")
    args, kw = started[0]
    assert "xfce4-terminal" not in args and args[-1] == "odd1-chat"
    assert kw["env"]["ODD1_AGENT_ROLE"] == "odd1-agent:chat" and "CLAUDE_CODE_CHILD_SESSION" not in kw["env"]
