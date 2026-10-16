# caretaker
Watch the machine's health and the foreground program's resource pressure, report it plainly, and repair failed services.
Milestone: M1 Caretaker (diagnose, repair, verify on a live machine); its pressure readings are the base for M2's OS-level slack.

## What it watches
Every 60 s the `odd1-caretaker` systemd unit takes one look: pressure stall information (PSI) for cpu, memory and
io; memory, load and disk use; failed systemd units; and the program owning the focused window on DISPLAY :0
(via xdotool), with its resident memory and its cgroup's memory use and pressure. Pressure is `ok`, `elevated`
(PSI some avg10 >= 10%) or `high` (>= 40%, or full avg10 >= 10%). Problems are stated in plain words
("systemd unit failed: nginx.service", "memory low: ...", "foreground soffice.bin under high pressure ...").
It only reads; it makes no model calls.

## Interface
- Agents: MCP server `caretaker` (registered for every agent): `caretaker_status` (freshness, service state,
  last level, problems, foreground program, recent repairs; no probing), `caretaker_pressure` (PSI now, system and
  foreground cgroup), `caretaker_repair` (restart a unit and verify it is active again), `caretaker_health` (what
  the person's health icon shows).
- Command line: `odd1-caretaker status | pressure | look | once | repair UNIT | watch | health | panel | popup`, and
  `odd1-caretaker run [--memory-mib N --cpu-percent N --tasks N --runtime-seconds N] -- CMD ...`, which runs a
  command in its own transient systemd unit under those limits and reports peak memory, most tasks, systemd's result
  and an outcome (`success`, `command-failed`, `resource-oom`, `runtime-limit`, `telemetry-incomplete`); the
  command's own output passes through unchanged. Use it to size a program before living with it.
- Files in `/var/lib/odd1/caretaker/` (`$ODD1_CARETAKER_STATE`): `health.json` is the freshness authority (`ok`,
  `last_success_at`, `last_error_at`, `last_error`; a failed look keeps the last success time and never passes for a
  fresh one), `latest.json` and `latest.md` the last look, `history.jsonl` about a day of looks, `actions.jsonl`
  every repair with its result.
- Automatic repair only for units listed in `odd1.caretaker.restartUnits` (module.nix), at most 3 restarts an hour
  each; after that the unit is reported as "keeps failing" and left for diagnosis. Restarts use `sudo -n systemctl`.

## The person's health icon
The top bar (desktop's `xfce4-panel.xml`, xfce4-genmon-plugin every 10 s) shows `odd1-caretaker panel`: a pulse
icon, green (all good), amber (needs a look) or red (something is wrong), with the reasons as its tooltip. A click
runs `odd1-caretaker popup`: a small window under the bar (GTK 3, the night palette, refreshed every 5 s) with
memory, disk, load and pressure, failed services, which role works on which ticket, runs today, what waits for the
person and the manager, open chats, the last backups and the running commit with rollbacks in 24 h. A second click,
Esc or a click elsewhere closes it. `odd1-caretaker health [--json]` prints the same (`health.py`: each source read
alone with a short timeout, so one broken source shows as a reason and never hides the rest). Red: a failed
service, memory or disk >= 92%, a minute's pressure >= 60%, the daily backup over 3 days or the hourly over a day
old. Amber: 85%, 25%, 36 h, 3 h; a stale caretaker; a failed (rolled back) last deploy. Tickets waiting for the
person are listed, not coloured: they are not a fault.

## Tests
`python -m pytest components/caretaker/tests -q` anywhere (fake /proc, cgroup and systemctl); the MCP client test
needs the `mcp` package and the live look needs Linux with PSI, both skip otherwise. On Odd1: `odd1-caretaker-test`.

## Next
- Give the foreground app slack under memory/CPU pressure: when PSI rises, raise its cgroup's `cpu.weight` and
  `memory.low`, and measure that it helps before keeping it.
- Pause or shrink background agents under pressure (cgroup `cgroup.freeze`, lower `cpu.weight`, `memory.high`), and
  resume them when pressure falls.
- Watch Odd1's background agents' budgets and failures (token spend, repeated failures, stuck runs) from a
  structured source, not from parsing run summaries.
- Agent process management (M4) as its own component: the supervisor project's task ledger and systemd launcher
  (`resident.py`, `systemd_adapter.py`, `reconciler.py`, `coordinator.py`) are tested pieces but were never joined
  into a working manager; start from them when that work begins.
- Repair beyond restart: diagnose from the unit's journal before restarting, and verify with the service's own
  check (e.g. an HTTP probe), not only `is-active`.
