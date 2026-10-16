# Architecture

Odd1 = NixOS VMs + the components in this repository. This map lists every component; `tools/check.py --map`
prints the generated part from each README's first line. Keep the "connections" notes current by hand.

## System (nix/)
- `nix/base.nix`: the machine-independent OS: user `agent`, the screen (X display :0 1280x800, window manager,
  VNC + noVNC on :6080 for the person; Remote Desktop next), AT-SPI, the app collection (Firefox, LibreOffice,
  GIMP, Inkscape, VSCodium, jEdit, Gnumeric, FeatherPad, ...), xdotool, tesseract.
- `nix/odd1.nix`: the Odd1 module: every component's module.nix, the MCP registry, the agent systems registry
  (`odd1.agents`, /etc/odd1/agents.json: what a person can talk to; the desktop's New chat menu lists it), the default coding agent
  (Claude Code, with the person's own sign-in; other agent harnesses can plug in through the same MCP servers).
- `nix/hosts/<host>.nix`: one per kind of machine (disks, boot, network); it imports base.nix and odd1.nix and is
  all `/etc/nixos/configuration.nix` imports. `dev-vm.nix` is the development VM (QEMU, egress proxy).

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
| tasks | M4 | Persistent work for role agents (housekeeper, update-watcher): store with enforced rules, a runner per role | tasks_create/list/show/approve/claim/renew/release/log/finish |
| agents | M4 | The Agents app: health and settings of every agent system (roles, tasks, Claude Code, tools) | (none: its changes are tasks' and role files) |
| caretaker | M1 | Machine health and foreground pressure (PSI, cgroups); restarts listed failed units | caretaker_status/pressure/repair |

## Connections
- Claude Code (the person's front agent, and any other agent harness plugged in) reaches every component through MCP.
- `screen` is the screen-model service (★): it fuses X11 geometry and stacking with `any-app`'s readers (AT-SPI,
  OCR). Grow readers in `any-app`, fusion in `screen`. With GNOME_ACCESSIBILITY=1 (set by screen's module) a
  visible Firefox's page joins it over AT-SPI; `browser` stays the precise, policy-checked view of headless pages.
- `any-app-broker` frames are meant to feed `any-app`; resource slices belong to Nix and `caretaker`.
- `agent-desktop` is where agents run their own apps; `screen` reads and drives both displays (display="agent").
  Its apps run in `odd1-agent.slice`, which `caretaker` can deprioritize or freeze for the person.
- `intent` is the safe hands for file changes: plan -> approve -> apply -> undo.
- `caretaker` watches pressure; giving the foreground program slack and pausing background agents is its next step (M2).
- Talking to agents: every agent system registers in `odd1.agents` (title, command); the desktop's chat column
  opens a tab per conversation, labelled by system. Claude Code is one; `tasks` registers its console.
- `agents` is the person's window onto all of it: it reads and changes `tasks` (store, role files) and reads
  `caretaker`, the registries and Claude Code; no state of its own.
- Decisions: routine ones go to the `tasks` manager role (within tags the person delegates), the rest to the person
  (desktop notification from `odd1-tasks-watch`); every reader catches up from the task log (`changes --since`).
- Components' Python is `pkgs.odd1Python` (nix/odd1.nix): Python 3 with the pins in `nix/python-updates.json`,
  written by `tasks`' updater role.
- Changes by agents reach main through `tasks`' fixer: a worktree per ticket, a full build and tests, then a
  fast-forward and a switch from a clean checkout (never the shared working tree).
- `tasks` is the agent process manager: `odd1-tasks-runner@<role>` starts Claude Code headless per approved task, under
  the role's permissions and budgets; tasks are proposed by anyone, approved by the person. Its runs' cost and
  failures are the structured source `caretaker` should watch; budgets beyond turns and minutes grow there.
