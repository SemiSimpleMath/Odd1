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
| screen | ★ | Each desktop as one model: X11 + AT-SPI + OCR in screen pixels, stable ids, changes, verified clicks | screen_view/changes/at/find/click/set_text/type |
| agent-desktop | M4 | A second X display (Xephyr :1) where agents run apps with their own pointer and keyboard | desktop_run/status/stop/show/hide |
| any-app | ★ | Any window as JSON: AT-SPI tree when available, OCR words otherwise | app_describe, app_ocr, app_screenshot |
| any-app-broker | ★ | Private Xvfb sessions driven through a capability-gated broker (test harness for now) | (planned) |
| intent | M4 | Plain-words file intents: plan, approve per action, apply transactionally, verify, undo | intent_plan/apply/undo/history |
| caretaker | M1 | Machine health and foreground pressure (PSI, cgroups); restarts listed failed units | caretaker_status/pressure/repair |

## Connections
- Claude Code (the person's front agent) and Orbit's background agents reach every component through MCP.
- `screen` is the screen-model service (★): it fuses X11 geometry and stacking with `any-app`'s readers (AT-SPI,
  OCR). Grow readers in `any-app`, fusion in `screen`. With GNOME_ACCESSIBILITY=1 (set by screen's module) a
  visible Firefox's page joins it over AT-SPI; `browser` stays the precise, policy-checked view of headless pages.
- `any-app-broker` frames are meant to feed `any-app`; resource slices belong to Nix and `caretaker`.
- `agent-desktop` is where agents run their own apps; `screen` reads and drives both displays (display="agent").
  Its apps run in `odd1-agent.slice`, which `caretaker` can deprioritize or freeze for the person.
- `intent` is the safe hands for file changes: plan -> approve -> apply -> undo.
- `caretaker` watches pressure; giving the foreground program slack and pausing background agents is its next step (M2).
