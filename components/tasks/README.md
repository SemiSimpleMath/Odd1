# tasks
Hand persistent background work to role agents (Claude Code), each claiming only its own tasks, under rules the store enforces and budgets the runner enforces.
Milestone: M4 (agent-native OS: an agent process manager, with turns, minutes and runs per day as budgeted resources).

Simple requests stay with the person's own Claude Code; this is for work that persists: recurring checks,
maintenance, follow-ups that wait for approval.

## Run it, approve, read logs
```
odd1-tasks list                          # every task: id, status (claimant), tags, goal
odd1-tasks show 3                        # one task and its whole log: claims, notes, runs, checks, results
odd1-tasks approve 3                     # a person approves a proposed task (or unblocks a blocked one)
odd1-tasks create "Disk use under 80%" --done-when 'test $(df --output=pcent / | tail -1 | tr -dc 0-9) -lt 80' \
    --tag housekeeping --resource /tmp --turns 10 --minutes 5 [--every 1d] [--depends-on 2] [--priority 5]
systemctl status odd1-tasks-runner@housekeeper    # the runners; journalctl -u 'odd1-tasks-runner@*' -f
```
Talk to it: **+ New chat > Task manager** in the desktop's chat column, or `odd1-tasks console`: Claude Code in
`/var/lib/odd1/tasks/console` (its `CLAUDE.md` and `settings.json`, yours to edit) that opens by saying what the
task manager is doing, with `odd1-tasks status` as its status line (every 5 s). Approving and stopping a task
(`release --blocked`; the runner stops its agent at the next lease renewal) always ask you first; a note on a task
(`tasks_log`) reaches its next run, since a running agent cannot be interrupted.

Agents use the same operations through the MCP server `tasks` (`tasks_create/list/show/approve/claim/renew/
release/log/finish`); the caller is `$ODD1_TASKS_AGENT` when a runner started it, otherwise `person:$USER`.

## The store: /var/lib/odd1/tasks/tasks.db (SQLite)
A task: goal (one sentence, the outcome), done_when (shell; exit 0 = done), tags, priority, budget (max_turns,
max_minutes), depends_on, resources (paths/services it changes), schedule (`1d`, `12h`, ...), created_by, status,
claim (claim_agent, lease_until), attempts/max_attempts (3), and the append-only `log` table. Read and change it with
SQL if you like (`agents` holds each role's tags); triggers hold the rules for every writer:
- New tasks start **proposed**; only a person approves (`odd1-tasks approve`). Each later run of an approved recurring
  task is created **ready** without asking again, one open run at a time.
- proposed>ready>claimed>verifying>done; claimed>ready (release, or lease expired) | blocked; blocked>ready (a
  person approves again); verifying>ready (attempts left) | parked (used up). Nothing is deleted; approved tasks
  are fixed.
- A claim is atomic and a lease (10 min, renewed by the runner every 2 min). The claimer must hold every tag of the
  task, its dependencies must be done, and no claimed or verifying task may share a resource (`/tmp` and `/tmp/x`
  overlap). An expired lease returns the task to ready; the next run's prompt holds the whole log to resume from.
- done only when a done_when check, run by the runner and logged, passed after the last finish.

## Runners and roles
`odd1-tasks-runner@<role>` (one systemd unit per role): every 30 s, with no model call, it rereads the role's config,
expires leases, starts due recurring runs, and claims a ready task it holds every tag for, unless the role used its
runs per day. Then it runs `claude -p --permission-mode dontAsk --settings <role>/settings.json
--strict-mcp-config` (only the tasks MCP server) in the role's folder with the goal, check, budget and log on stdin,
with `--max-turns` = min(task, role) and a kill at min(task, role) minutes. Then it runs done_when itself and
records done, retry or parked. Cost and turns of each run are in the log (`run-end`).

Role folders, `/var/lib/odd1/tasks/roles/<role>/`: `CLAUDE.md` (the role), `settings.json` (Claude Code permissions:
an allow-list; anything else is refused), `role.toml` (tags, runs_per_day, max_turns, max_minutes, model, starter
tasks; edit freely, no rebuild), helpers, `reports/`. Seeded from `roles/` here where files are missing, so edits stay.
- **housekeeper** (`housekeeping`): reports disk use; deletes only through `clean.sh` (files owned by agent unchanged
  for over 7 days, in ~/.cache or /tmp). Anything else it would delete becomes a proposal tagged `needs-person`.
- **update-watcher** (`updates`): a daily recurring task; `check-versions.py` compares Claude Code (npm) and the
  Python packages components install (from the Nix build, `/etc/odd1/tasks/python-packages.json`; PyPI) and it
  proposes "Update X from a to b" tagged `apply-update`. It never applies updates (no role holds that tag yet).
Each role proposes one starter task when its runner first starts; approve it to begin.

Limits: agents and runners run as the same Unix user `agent`, so the role's permissions (no Bash beyond the listed
commands, no SQL) keep an agent from approving or writing the store directly; a separate user per role would make
that a hard boundary.

## Tests
`python -m pytest components/tasks/tests -q` (on Odd1: `odd1-tasks-test`), offline, with a stub `claude`
(`tests/stub_claude.py`): roles claim only their tags, proposals wait, recurring runs start alone, a killed runner's
task returns after its lease with its log, checks decide done/retry/park, turn/time/runs-per-day budgets, the
rules against raw SQL. The MCP client test needs the `mcp` package (it skips otherwise).

## Next
- A role that applies approved updates (through the repository and nixos-rebuild), and the Odd1 app showing tasks and
  roles. Token budgets in dollars per role (`--max-budget-usd`) once runs' costs are known.
