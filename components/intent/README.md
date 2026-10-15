# intent
State a file intent in plain words, review the exact plan, approve only the actions you choose, and get SHA-256-verified results you can undo.
Milestone: M4 (agent-native OS: the user-facing assistant layer, where a person states an intent and the system plans and applies it).

## What it does
One shared engine (`odd1_intent/engine.py`) turns a request into a `Plan` of concrete actions, changes nothing until
specific actions are approved, then applies them as one transaction with a recorded before-image, verifies every
result by SHA-256, and can undo the latest (or a named) transaction if nobody has changed the files since.
It makes no model or network calls: understanding is deliberately narrow and deterministic. Two intents today:
- `Update files in "<folder>" with the ones from "<folder>"`: direct children only. Missing files become ADD (unselected,
  opt in), newer same-name files become REPLACE (selected; the old bytes are kept for undo), the rest SKIP.
- `Make a backup of "<file>"`: one ADD to `<file>.bak`, refusing symlinks and existing targets.
Anything else is refused with the supported wording, never guessed.

Safety model, in short: preview binds content hashes and parent-directory identities; apply re-checks them, installs
with Linux `renameat2` (no-clobber add, atomic exchange for replace) through pinned directory descriptors, fsyncs, and
rolls back on failure, naming any `.orbit-*` recovery artifacts it had to keep. A per-user `flock` serializes engine
processes. History is schema-checked and read with no-follow, single-link, owner and size checks. Not proven: power-loss
durability, safety against arbitrary concurrent writers. Linux only (fails closed without `renameat2`).

## Use
- Agents: the MCP server `intent` (registered for every agent): `intent_plan(request)` -> `plan_id` and the actions,
  nothing changed; `intent_apply(plan_id, indexes)` applies exactly those ADD/REPLACE indexes (each plan once);
  `intent_undo(transaction_id="")`; `intent_history()`.
- Command line: `odd1-intent 'Update files in "/path/project" with the ones from "/path/Downloads"'` prints the plan,
  asks for action numbers (Enter = defaults, `all`, `none`), shows the final list and requires typing `YES`.
  `odd1-intent --undo [ID]`. End of input or any parse failure changes nothing.
- Desktop: `odd1-intent-gui` (or `systemctl --user start odd1-intent-gui`) opens a Tk window on DISPLAY :0: request,
  the two folders with Browse, preview with a checkbox per action, approve, history, undo latest. Editing the request
  or folders, or a failed apply, retires the shown plan so a stale plan can never be approved.
- Python: `from odd1_intent.engine import parse, apply, undo, history`.
- State: `$ODD1_INTENT_STATE`, `/var/lib/odd1/intent/` on Odd1 (history.json, transactions/, engine.lock; mode 0700,
  owned by `agent`); `~/.local/share/odd1-intent/` elsewhere.

## Tests
`python -m pytest components/intent/tests -q` (on Odd1: `odd1-intent-test`). Engine, CLI, GUI-controller and MCP tests
need Linux (they skip elsewhere with that reason); the MCP test needs the `mcp` package (v1, FastMCP); the real-Tk
window test needs `DISPLAY` and tkinter. `test_gui.py` and `test_gui_flow.py` stub Tk and run headless. Use a local
filesystem for the temp dir (9p/virtiofs mounts may lack `renameat2`). Two engine tests are marked expected-failure:
they were already failing in the migrated workspace (their injected faults no longer land on the step they target).

## Next
- Fix the two stale rollback tests by targeting the engine's named `_failure_boundary` seams instead of fsync counts.
- Finish the descriptor audit: some rollback/undo fsyncs and cleanup still use textual paths beside pinned exchanges.
- Bring over the read-only `recovery_inspector` (lists and hashes `.orbit-*` residue after an interrupted transaction).
- Widen what a person can ask for (recursive sync, move, rename, delete-to-trash) without guessing: new intents as
  explicit plan kinds with the same preview, approve, verify, undo contract.
- Power-loss durability evidence needs a reset-capable VM; process-kill probes alone cannot show it.

## Open question: Intent and Claude Code
Claude Code is the person's front agent in Odd1, so Intent should not be a competing chat front end. The likely shape:
Intent is the safe hands for file changes that Claude Code (and every other agent) calls through the MCP server, with
the person approving the exact plan; the GUI stays as the direct, model-free path for people. Open: whether Claude Code
should route its own file edits through `intent_plan`/`intent_apply` (a skill or hook that turns any multi-file change
into an approvable, undoable transaction), and whether the GUI should autostart with the session (it does not now).
