# chat
Claude Code as a chat window: bubbles, Markdown, code folded away, tools as one-line notes, permission requests as Allow/Deny cards, pictures pasted with Ctrl+V.
Milestone: M4 (agent-native OS): the user-facing assistant layer; the person's conversations without a terminal.

## Use
- From the desktop: **+ New chat > Chat** in the chat column (it docks there as a tab like the terminal chats).
- `odd1-chat [--cwd DIR] [--resume SESSION]`: a window anywhere. Enter sends, Shift+Enter breaks the line, Ctrl+V
  pastes a picture, Esc interrupts Claude. When Claude needs the person and the window is not in front, it marks
  itself urgent (the tab's orange ball).

## How it works
- `protocol.py`: Claude Code's stream-json protocol as pure functions (the one the Claude Agent SDK speaks, without
  the SDK: it needs `mcp` >= 1.23 and bundles its own 250 MB CLI). `claude --input-format stream-json
  --output-format stream-json --include-partial-messages --permission-prompt-tool stdio --permission-mode auto`:
  auto mode approves routine actions itself; the permission requests it leaves to the person arrive as `control_request` (`can_use_tool`), answered with a `control_response` (allow + input, or deny).
- `session.py`: the process and a reader thread; `app.py`: GTK window, WebKit transcript (`page.html`), native text
  field. `render.py`: Markdown with raw HTML shown as text; a list right after a line is a list (as in CommonMark); code longer than 6 lines folds.
- The conversation is the person's own session: Claude Code's session variables are not inherited.
- No GPU: WebKit draws on the CPU (environment set in `app.py`; Mesa via `hardware.graphics`).

## Tests
`python -m pytest components/chat/tests -q` (offline: the protocol against recorded CLI lines, rendering). On Odd1:
`odd1-chat-test`. Live check: on the agent desktop, a message, a Write permission card answered with Allow, the file
written, the reply rendered with its code folded.

## Next
- Resume and history: list past conversations (`--resume`), keep the transcript across window restarts.
- "Always allow" on a card (the request's permission suggestions), and slash commands (/model, /clear).
- Becoming the default chat once the person prefers it to the terminal.
