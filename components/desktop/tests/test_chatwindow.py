"""The chat window's rules, offline: the Claude command line a chat's settings make, and where an undocked chat
opens."""
from odd1_desktop import chatwindow, layout

SESSION = "6fbe65a9-5fe9-468e-8e1a-b5e543062397"


def test_the_chat_command_s_own_settings_show_in_the_gear_and_are_not_doubled():
    argv, settings = chatwindow.split_settings("claude --permission-mode auto --name=x")
    assert argv == ["claude", "--name=x"] and settings["mode"] == "auto" and settings["verbose"] is False
    line = chatwindow.claude_argv("claude --permission-mode auto", {**settings, "mode": "plan"}, SESSION)
    assert line == ["claude", "--session-id", SESSION, "--permission-mode", "plan"]


def test_code_being_written_is_hidden_by_default_and_shown_with_verbose():
    quiet = chatwindow.claude_argv("claude", dict(chatwindow.DEFAULTS), SESSION)
    assert quiet == ["claude", "--session-id", SESSION]
    loud = chatwindow.claude_argv("claude", {**chatwindow.DEFAULTS, "verbose": True, "model": "haiku",
                                             "effort": "high"}, SESSION, resume=True)
    assert loud == ["claude", "--resume", SESSION, "--model", "haiku", "--effort", "high", "--verbose"]


def test_only_claude_chats_get_claude_settings():
    assert chatwindow.is_claude("claude --permission-mode auto") and chatwindow.is_claude("/usr/bin/claude")
    assert not chatwindow.is_claude("odd1-tasks console") and not chatwindow.is_claude("")


def test_the_shell_line_keeps_the_window_after_the_agent_and_quotes_arguments():
    assert chatwindow.shell_line(["claude", "--name", "it's"]) == "claude --name 'it'\"'\"'s'; exec bash"


def test_only_a_saved_session_is_resumed(tmp_path):
    assert not chatwindow.session_saved(SESSION, home=str(tmp_path))
    folder = tmp_path / ".claude" / "projects" / "-srv-odd1"
    folder.mkdir(parents=True)
    (folder / f"{SESSION}.jsonl").write_text("{}\n")
    assert chatwindow.session_saved(SESSION, home=str(tmp_path))


def test_an_undocked_chat_opens_in_the_middle_of_the_work_area_and_the_next_steps_aside():
    area = (0, 34, 1000, 700)
    assert layout.undocked_box(area) == (150, 139, 700, 490)
    assert layout.undocked_box(area, others=1)[:2] == (182, 171)
    assert layout.undocked_box(area, others=5) == layout.undocked_box(area)


def test_an_opening_prompt_goes_with_the_first_start_only():
    first = chatwindow.claude_argv("claude", dict(chatwindow.DEFAULTS), SESSION, prompt="-sum it up")
    assert first == ["claude", "--session-id", SESSION, "--", "-sum it up"]
    again = chatwindow.claude_argv("claude", dict(chatwindow.DEFAULTS), SESSION, resume=True, prompt="-sum it up")
    assert again == ["claude", "--resume", SESSION]
