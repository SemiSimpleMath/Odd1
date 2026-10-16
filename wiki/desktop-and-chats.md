# The desktop and chats
- The person's screen is DISPLAY=:0, reached over Remote Desktop. Focus belongs to whoever used a window last: never
  take the person's pointer or keyboard without asking (`screen_click` via the pointer and `screen_type` need
  `person_approved=true`). Run apps on the agent desktop (:1) instead (MCP `agent-desktop`).
- See a window: `odd1-any-app screenshot WINDOW -o out.png` (or `--screen`), then read the PNG.
- Chats live in the chat column (New chat). A terminal copies what you select; Ctrl+C there interrupts Claude.
- Remote Desktop shares drives at `~/Windows` and the clipboard; only the first open connection gets them.
- Details: `components/desktop/README.md`, `components/any-app/README.md`, `components/screen/README.md`.
