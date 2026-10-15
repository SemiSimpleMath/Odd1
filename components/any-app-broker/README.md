# any-app-broker
Run a GUI program in its own private X session (Xvfb) and drive it through a capability-gated broker: observe a frame, then click, type or press keys against that frame.
Milestone: ★ (any app, through interfaces agents write themselves). The isolated act-and-observe loop for apps an agent is learning to drive.

## Interface
- Daemon: `odd1-any-app-broker [--socket PATH] [--root DIR] [--evidence-root DIR] [--aggregate-limits --owner-unit UNIT] serve`
  listens on a Unix socket (default `$XDG_RUNTIME_DIR/orbit-any-app/broker.sock`, mode 0600); one JSON object per
  line in, one out: `{"ok": true, "result": ...}` or `{"ok": false, "error", "error_type"}`.
- Client: `odd1-any-app-broker request '{"op":"create"}'`.
- Operations: `create` (new Xvfb :91-:120 with its own Xauthority and HOME, starts the app; returns `session_id` and a
  secret `capability`); then, with both: `observe` (PNG frame as base64 plus `frame_id`), `click {x,y}`,
  `type {text}`, `key {key}` (small allowlist), `wait {seconds}`, `close`. Every input must name the current
  `frame_id`; a stale or foreign frame is refused, and each input advances the revision.
- Limits: 3 sessions, 16 workers, 32 KiB requests, 10 s per dispatch, 10 min idle expiry, per-child rlimits via
  `prlimit`, optional runtime cgroup slices (`--aggregate-limits`), 64 MiB evidence budget.
- Today the session always runs the bundled Tk form app (`any_app_broker/form_app.py`): it is the test target.

Its MCP tools, once it launches arbitrary programs: `session_create(command)`, `session_observe`, `session_click`,
`session_type`, `session_key`, `session_wait`, `session_close`, with the capability held by the server, not the agent.

## Tests
`python -m pytest components/any-app-broker/tests -q`. `test_broker_unit.py` is offline (no X server) but needs
Linux (fcntl, Unix sockets, process groups) and skips elsewhere. `test_live.py` runs `tests/live/smoke.py` (two
sessions, observe, type, click, stale-frame denial, saved state, restart cleanup) and the worker-cap and load
probes; it skips without Xvfb, xauth, xdpyinfo, xdotool, ImageMagick and prlimit. The aggregate-limits probes
(`live_recovery_smoke.py`, `abrupt_recovery_probe.py`) create user-systemd slices: run them by hand on the Desktop.
On Odd1: `odd1-any-app-broker-test`.

## Next
- `create` takes the program to run (and its own window title), so the broker serves real apps, then the MCP server.
- Declare the resource slices in module.nix (systemd) instead of setting runtime properties from Python, and run the
  broker as a user service; that removes most of the slice-recovery code.
- Feed `observe` frames to `any-app` (`app_ocr`) and the app's AT-SPI tree, so actions target described elements.
