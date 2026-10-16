"""The stream-json protocol: what we send, and the events we make of what the CLI writes (lines recorded from
claude 2.1.301)."""
import json

from odd1_chat import protocol


def test_command_streams_both_ways_and_takes_permission_prompts():
    args = protocol.command("claude", resume="abc")
    assert args[0] == "claude" and "--include-partial-messages" in args
    assert args[args.index("--permission-prompt-tool") + 1] == "stdio"
    assert args[args.index("--permission-mode") + 1] == "auto"
    assert args[args.index("--input-format") + 1] == args[args.index("--output-format") + 1] == "stream-json"
    assert args[args.index("--resume") + 1] == "abc"


def test_the_conversation_is_not_a_child_of_its_starter():
    assert protocol.environment({"CLAUDECODE": "1", "CLAUDE_CODE_CHILD_SESSION": "1", "HOME": "/h"}) == {"HOME": "/h"}


def test_user_message_with_and_without_images():
    plain = json.loads(protocol.user("hi"))
    assert plain["type"] == "user" and plain["message"] == {"role": "user", "content": "hi"}
    pic = json.loads(protocol.user("what is this?", [("image/png", "AAAA")]))
    assert [b["type"] for b in pic["message"]["content"]] == ["image", "text"]
    assert pic["message"]["content"][0]["source"] == {"type": "base64", "media_type": "image/png", "data": "AAAA"}


def test_answers_allow_with_the_input_or_deny_with_a_reason():
    allow = json.loads(protocol.answer("r1", True, {"file_path": "/x"}))
    assert allow == {"type": "control_response", "response": {"subtype": "success", "request_id": "r1",
                     "response": {"behavior": "allow", "updatedInput": {"file_path": "/x"}}}}
    deny = json.loads(protocol.answer("r2", False))["response"]["response"]
    assert deny["behavior"] == "deny" and deny["message"]


RECORDED = [
    '{"type":"system","subtype":"init","session_id":"s1","model":"claude-opus-5-5","tools":["Write"]}',
    '{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Do"}}}',
    '{"type":"stream_event","event":{"type":"message_start"}}',
    '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Write","input":{"file_path":"/w/hello.txt","content":"hi"}}]}}',
    '{"type":"control_request","request_id":"req_7","request":{"subtype":"can_use_tool","tool_name":"Write","display_name":"Write","description":"hello.txt","input":{"file_path":"/w/hello.txt","content":"hi"}}}',
    '{"type":"assistant","message":{"content":[{"type":"text","text":"Done"}]}}',
    '{"type":"rate_limit_event"}',
    '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":0.11,"session_id":"s1","result":"Done"}',
    'not json',
]


def test_recorded_lines_become_events():
    found = [e for raw in RECORDED for e in protocol.events(raw)]
    assert [e["kind"] for e in found] == ["ready", "delta", "tool", "permission", "text", "done"]
    assert found[0] == {"kind": "ready", "session": "s1", "model": "claude-opus-5-5"}
    assert found[2]["summary"] == "Writing hello.txt"
    assert found[3]["id"] == "req_7" and found[3]["prompt"] == "Claude wants to write hello.txt"
    assert found[3]["detail"] == "/w/hello.txt"
    assert found[5] == {"kind": "done", "session": "s1", "cost": 0.11, "error": False, "text": "Done"}


def test_tool_summaries_say_what_was_done_not_the_code():
    assert protocol.summary("Bash", {"command": "rm -rf x", "description": "Clean the build"}) == "Clean the build"
    assert protocol.summary("Bash", {"command": "ls"}) == "Running a command"
    assert protocol.summary("Edit", {"file_path": "/a/b/shell.py"}) == "Editing shell.py"
    assert protocol.summary("mcp__screen__screen_click", {}) == "Using screen: screen click"
    assert protocol.summary("Mystery", {}) == "Using Mystery"


def test_a_shell_command_card_shows_the_command_itself():
    prompt, detail = protocol.permission_text("Bash", {"display_name": "Run", "description": "Clean the build",
                                                       "input": {"command": "rm -rf build/"}})
    assert prompt == "Claude wants to run a command: Clean the build" and detail == "rm -rf build/"
    assert protocol.permission_text("Read", {"title": "Claude wants to read secrets.txt",
                                             "input": {"file_path": "/x/secrets.txt"}}) == \
        ("Claude wants to read secrets.txt", "/x/secrets.txt")
