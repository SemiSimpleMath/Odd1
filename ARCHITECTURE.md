# Architecture

Odd1 = NixOS VMs + the components in this repository. This map lists every component; `tools/check.py --map`
prints the generated part from each README's first line. Keep the "connections" notes current by hand.

## Base system (kernel-owned, not in this repository)
- Desktop VM: Xvfb :0 (1280x800), openbox, noVNC on :6080 for people, AT-SPI for accessibility, the app collection
  (Firefox, LibreOffice, GIMP, Inkscape, VSCodium, jEdit, Gnumeric, FeatherPad, ...), xdotool, tesseract.
- Machine VM: sshd, nginx on :80 serving /srv/www, Python and common CLI tools.
- Model access for agents on the VMs: 127.0.0.1:8099 (metered by Orbit's kernel). Claude Code on the Desktop uses
  the user's own sign-in instead.
- Network: package registries only (PyPI, the Nix cache; npm and Anthropic for the Desktop). No general internet.

## Components
Every component's agent entry point is an MCP server of the same name, registered for every agent
(/etc/odd1/mcp-servers.json); `python tools/check.py --map` prints the purpose lines.

| Component | Milestone | What it is | MCP |
|---|---|---|---|
| libreoffice | ★ | Calc through its own object model (UNO), on the visible desktop | calc_open/read/write/formula/save/close |
| browser | ★ | Firefox-rendered pages as an element-id page model; act by id | browser_open/view/click/type/select/submit/find/fetch/close |
| any-app | ★ | Any window as JSON: AT-SPI tree when available, OCR words otherwise | app_describe, app_ocr |
| any-app-broker | ★ | Private Xvfb sessions driven through a capability-gated broker (test harness for now) | (planned) |
| intent | M4 | Plain-words file intents: plan, approve per action, apply transactionally, verify, undo | intent_plan/apply/undo/history |
| caretaker | M1 | Machine health and foreground pressure (PSI, cgroups); restarts listed failed units | caretaker_status/pressure/repair |

## Connections
- Claude Code (the person's front agent) and Orbit's background agents reach every component through MCP.
- For a web page, `browser` (DOM) is the precise view; `any-app` (accessibility/OCR) covers every other window.
  Both are inputs to the future screen-model service (★); merge there rather than growing separate views.
- `any-app-broker` frames are meant to feed `any-app`; resource slices belong to Nix and `caretaker`.
- `intent` is the safe hands for file changes: plan -> approve -> apply -> undo.
- `caretaker` watches pressure; giving the foreground program slack and pausing background agents is its next step (M2).
