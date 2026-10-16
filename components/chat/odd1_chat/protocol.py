"""Claude Code's stream-json protocol, as pure functions: the command line, the lines we write, and the events we
make of the lines it writes. (The same protocol the Claude Agent SDK speaks; spoken here with the standard library,
so the chat window needs only the installed `claude`.)

Events, one per CLI line that matters:
    {"kind": "ready", "session": id, "model": name}            the session started (system/init)
    {"kind": "delta", "text": str}                              more of the reply being written
    {"kind": "text", "text": str}                               a finished text block of the reply
    {"kind": "tool", "id": str, "name": str, "summary": str, "input": dict}   Claude used a tool
    {"kind": "permission", "id": request id, "tool": name, "prompt": str, "detail": str, "input": dict}
    {"kind": "done", "session": id, "cost": float, "error": bool, "text": str}  the turn ended
"""
import json
import os

PERMISSION_PROMPT = "stdio"  # permission requests come to us as control requests


def command(claude="claude", resume=None, model=None):
    """The CLI's arguments for a streamed conversation whose permission prompts come to us."""
    args = [claude, "--output-format", "stream-json", "--input-format", "stream-json", "--verbose",
            "--include-partial-messages", "--permission-prompt-tool", PERMISSION_PROMPT]
    if resume:
        args += ["--resume", resume]
    if model:
        args += ["--model", model]
    return args


def environment(environ):
    """The caller's environment without Claude Code's session markers: this conversation is the person's own, not a
    child of whatever started the window."""
    return {key: value for key, value in environ.items() if not key.startswith("CLAUDE")}


def line(obj):
    return json.dumps(obj) + "\n"


def initialize(request_id):
    return line({"type": "control_request", "request_id": request_id, "request": {"subtype": "initialize",
                                                                                   "hooks": None}})


def interrupt(request_id):
    return line({"type": "control_request", "request_id": request_id, "request": {"subtype": "interrupt"}})


def user(text, images=()):
    """A message from the person; images are (media_type, base64 data) pairs, placed before the text."""
    content = [{"type": "image", "source": {"type": "base64", "media_type": kind, "data": data}}
               for kind, data in images]
    content.append({"type": "text", "text": text})
    return line({"type": "user", "message": {"role": "user", "content": content if images else text},
                 "parent_tool_use_id": None, "session_id": "default"})


def answer(request_id, allow, tool_input=None, message="The person declined."):
    """The person's answer to a permission request."""
    response = {"behavior": "allow", "updatedInput": tool_input or {}} if allow else \
        {"behavior": "deny", "message": message}
    return line({"type": "control_response", "response": {"subtype": "success", "request_id": request_id,
                                                          "response": response}})


def summary(name, tool_input):
    """A short, plain description of a tool use as it happens (it may still be refused): what, never the code."""
    tool_input = tool_input or {}
    path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path") or ""
    target = os.path.basename(path) if path else ""
    if name == "Bash":
        return tool_input.get("description") or "Running a command"
    if name in ("Read",):
        return f"Reading {target}" if target else "Reading a file"
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"Editing {target}" if target else "Editing a file"
    if name == "Write":
        return f"Writing {target}" if target else "Writing a file"
    if name in ("Glob", "Grep"):
        return "Searching the files"
    if name in ("WebFetch", "WebSearch"):
        return "Looking on the web"
    if name in ("Task", "Agent"):
        return tool_input.get("description") or "Asking a helper agent"
    if name == "TodoWrite":
        return "Updating its plan"
    if name.startswith("mcp__"):
        parts = name.split("__")
        return f"Using {parts[1]}: {parts[-1].replace('_', ' ')}" if len(parts) >= 3 else f"Using {name}"
    return f"Using {name}"


def permission_text(tool, request):
    """What a permission card says: one sentence, and the detail the person decides on (the command itself for a
    shell command, the file for file tools)."""
    tool_input = request.get("input") or {}
    if request.get("title"):
        prompt = request["title"]
    elif tool == "Bash":
        about = request.get("description") or tool_input.get("description") or ""
        prompt = f"Claude wants to run a command: {about}" if about else "Claude wants to run a command"
    else:
        verb = (request.get("display_name") or tool).lower()
        target = request.get("description") or ""
        prompt = f"Claude wants to {verb} {target}".strip()
    if tool == "Bash":
        detail = tool_input.get("command", "")
    else:
        detail = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("url") or ""
    return prompt, detail


def events(raw):
    """The events one line from the CLI makes (most lines make none)."""
    try:
        msg = json.loads(raw)
    except ValueError:
        return []
    kind = msg.get("type")
    if kind == "system" and msg.get("subtype") == "init":
        return [{"kind": "ready", "session": msg.get("session_id"), "model": msg.get("model")}]
    if kind == "stream_event":
        event = msg.get("event") or {}
        delta = event.get("delta") or {}
        if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            return [{"kind": "delta", "text": delta.get("text", "")}]
        return []
    if kind == "assistant":
        found = []
        for block in (msg.get("message") or {}).get("content") or []:
            if block.get("type") == "text" and block.get("text", "").strip():
                found.append({"kind": "text", "text": block["text"]})
            elif block.get("type") == "tool_use":
                found.append({"kind": "tool", "id": block.get("id"), "name": block.get("name", ""),
                              "summary": summary(block.get("name", ""), block.get("input")),
                              "input": block.get("input") or {}})
        return found
    if kind == "control_request" and (msg.get("request") or {}).get("subtype") == "can_use_tool":
        request = msg["request"]
        tool = request.get("tool_name", "")
        prompt, detail = permission_text(tool, request)
        return [{"kind": "permission", "id": msg.get("request_id"), "tool": tool, "prompt": prompt,
                 "detail": detail, "input": request.get("input") or {}}]
    if kind == "result":
        return [{"kind": "done", "session": msg.get("session_id"), "cost": msg.get("total_cost_usd"),
                 "error": bool(msg.get("is_error")), "text": msg.get("result") or ""}]
    return []
