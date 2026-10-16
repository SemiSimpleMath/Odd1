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
- New tasks start **proposed** and need a decision: a person's, or an agent's within the tags a person delegated to
  it (`decides` in its role.toml: a task is the agent's to decide when one of its tags is delegated and none is
  `needs-person` or `manage`; never a task it proposed). Declining and cancelling (with a reason) work the same. Each later run of an approved recurring
  task is created **ready** without asking again, one open run at a time.
- proposed>ready>claimed>verifying>done; proposed>declined; open work (and a recurring task's series)>cancelled; claimed>ready (release, or lease expired) | blocked; blocked>ready (a
  person approves again); verifying>ready (attempts left) | parked (used up). Nothing is deleted; approved tasks
  are fixed.
- A claim is atomic and a lease (10 min, renewed by the runner every 2 min). The claimer must hold every tag of the
  task, its dependencies must be done, and no claimed or verifying task may share a resource (`/tmp` and `/tmp/x`
  overlap). An expired lease returns the task to ready; the next run's prompt holds the whole log to resume from.
- done only when a done_when check, run by the runner and logged, passed after the last finish.

## Who acts on what: the manager, the person, the change feed
`odd1-tasks attention [--for person|manager]` lists who has to act on what, derived from the store each time (never
stale): proposals, blocked and parked tasks, and approved tasks no role runs go to the **manager** role when their
tags are in its `decides`, otherwise to the **person**; so do escalations (`odd1-tasks escalate ID REASON`, until a
person acts on the task), decisions the manager left longer than `escalate_after_minutes` (it is paused, down, or
stuck), and stopped runners. The manager runs only while it has items (its recurring task's `run_if`), so there are
no idle model calls. `odd1-tasks-watch` (user service) notifies the person of their new items, once each (again after
a day if still open), batched. A cancel reaches a running agent through its lease: the runner's next renewal fails
and it stops the agent. `odd1-tasks changes --since N` (MCP `tasks_changes`) gives everything after log entry N and
a new cursor, so any reader catches up from the one log instead of keeping its own copy.

## Asking the task manager: its inbox
For a question (not a bug or a piece of work: those are tickets), any agent leaves the task manager a message:
`tasks_ask(text, task_id?)` or `odd1-tasks ask TEXT [--task ID]`. It is signed with the asker's Claude Code session
name (found up the process tree in `~/.claude/sessions/<pid>.json`; a runner's agent is also known by its role).
An unanswered message is an attention item for the manager, so it wakes the manager's run (the person's if no
manager runs, or after `escalate_after_minutes`). The manager answers with `tasks_reply`, which records the answer
once (the `messages` table is append-only; a task's log keeps answers about it) and says where it goes: to the
asker's session, by its current name, sent with Claude Code's SendMessage as "@name The task manager answers your
message #N ...", so it arrives in the asker's conversation as a new event it reacts to. A role agent whose run has
ended gets the answers of the last day in its next run's prompt. A subagent has no session of its own: it passes
its name (`agent`), the answer goes to its parent session as "@parent, for your subagent NAME: ...", and the parent
passes it on with SendMessage (a finished subagent resumes from its transcript). Any asker can instead wait for the
answer (`wait_minutes`, up to 15; `odd1-tasks ask --wait MIN`) and get it as its call's result; the session still
gets the message, marked as had already, so a wait cut short loses nothing. `odd1-tasks inbox [--all]` lists the
messages.

## Runners and roles
`odd1-tasks-runner@<role>` (one systemd unit per role): every 30 s, with no model call, it rereads the role's config,
expires leases, starts due recurring runs, and claims a ready task it holds every tag for, unless the role used its
runs per day. Then it runs `claude -p --permission-mode dontAsk --settings <role>/settings.json
--strict-mcp-config` (only the tasks MCP server) in the role's folder with the goal, check, budget and log on stdin,
with `--max-turns` = min(task, role) and a kill at min(task, role) minutes. Then it runs done_when itself and
records done, retry or parked. Cost and turns of each run are in the log (`run-end`).

Role folders, `/var/lib/odd1/tasks/roles/<role>/`: `CLAUDE.md` (the role), `settings.json` (Claude Code permissions:
an allow-list; anything else is refused), `role.toml` (enabled: false pauses the role, tags, runs_per_day, max_turns, max_minutes, model, starter
tasks; edit freely, no rebuild), helpers, `reports/`. Seeded from `roles/` here: a file you edited stays yours; files you did not edit follow the repository
(`.seeded` records what was copied). The console folder is seeded the same way.
- **housekeeper** (`housekeeping`): reports disk use; deletes only through `clean.sh` (files owned by agent unchanged
  for over 7 days, in ~/.cache or /tmp). Anything else it would delete becomes a proposal tagged `needs-person`.
- **manager** (`manage`; decides `housekeeping`, `updates`, `friction`; `backlog_tags = ["friction"]`: approved
  tickets wait for people and their chats, they are not orphans): approves or declines routine proposals, unblocks or
  cancels, escalates anything irreversible, costly or unclear. Only a person approves its own starter.
- **fixer** (`friction` + every component, `all_components`): works approved friction tickets, one per run, each
  in its own git worktree on branch `task/<id>` made from main (`workspace = "git-worktree"`; the repository's
  CLAUDE.md applies there, the role's is appended). `odd1-tasks-check [COMPONENT...]`: fitness check, a full system
  build from the branch, the installed tests of the changed (and named) components. `odd1-tasks-land`: rebase on
  main, check again, fast-forward main, deploy that commit with `odd1-deploy` (components/deploy; `scripts/`). `review = true` in the
  role makes odd1-tasks-land refuse: the next step is review by other agents before
  landing (see Next). Starts at 1 run a day: raise it in the Agents app once its fixes are trusted.
- **updater** (`apply-update`): applies approved updates, one per run. Claude Code: `npm install -g` the version.
  A Python package of the components: `pin-python.py` pins it in `nix/python-updates.json` (PyPI's sdist URL and
  hash; a major version jump is refused, a person decides), which `nix/odd1.nix` applies to `pkgs.odd1Python`, the
  components' own Python (nothing else rebuilds); then `odd1-tasks-check` with the components that use it, and
  `odd1-tasks-land`. A pin keeps nixpkgs' recipe: it builds when the recipe still fits (PyGObject 3.50 -> 3.58 did);
  when it does not (selenium's patches, jsonschema's rpds-py), the updater escalates: that needs a newer nixpkgs.
  Starts at 1 run a day.
- **update-watcher** (`updates`): a daily recurring task; `check-versions.py` compares Claude Code (npm) and the
  Python packages components install (from the Nix build, `/etc/odd1/tasks/python-packages.json`; PyPI) and it
  proposes "Update X from a to b" tagged `apply-update`. It never applies updates (no role holds that tag yet).
Permission rules in a role's settings.json (checked with dontAsk): `Bash(cmd:*)` matches `cmd` followed by a space
and arguments, not `cmd@1.2`; `Bash(cmd*)` matches any suffix. Every command of a chain (`a && b`, `a | b`) must be
allowed; read-only ones (echo, tail, ls) always are. Each role proposes one starter task when its runner first starts; approve it to begin.

Limits: agents and runners run as the same Unix user `agent`, so the role's permissions (no Bash beyond the listed
commands, no SQL) keep an agent from approving or writing the store directly; a separate user per role would make
that a hard boundary.

## Tests
`python -m pytest components/tasks/tests -q` (on Odd1: `odd1-tasks-test`), offline, with a stub `claude`
(`tests/stub_claude.py`): roles claim only their tags, proposals wait, recurring runs start alone, a killed runner's
task returns after its lease with its log, checks decide done/retry/park, turn/time/runs-per-day budgets, the
rules against raw SQL. The MCP client test needs the `mcp` package (it skips otherwise).

## Next
- Tickets as pull requests (`review = true`): the fixer's checked branch becomes a review task for reviewer roles
  (agents deciding within delegated tags, like the manager), and lands after their approvals; a person only for
  what they escalate.
- Updating nixpkgs itself (the way forward when a pin does not fit), as a task the person approves.
- A role that applies approved updates (through the repository and nixos-rebuild), and the Odd1 app showing tasks and
  roles. Token budgets in dollars per role (`--max-budget-usd`) once runs' costs are known.
