# agents
One window for the health and settings of every agent system on Odd1: what each role is doing, tasks to approve, budgets to adjust.
Milestone: M4 (agent-native OS: the user-facing assistant layer; agents and their token budgets as things a person manages).

## Pages
- **Overview**: each role (on, paused, or its runner down; what it is working on; runs and cost today), what waits
  for you (proposed, blocked, parked; Show opens them), today's runs, cost and results, and the machine's pressure
  and problems from the caretaker.
- **Tasks**: by view (waiting for you, running, ready, done, all), newest first; open one for its check, budget,
  log and actions: Approve, Unblock, Stop (blocks it; the runner stops its agent within 2 minutes), and a note for
  its next run.
- **Roles**: per role, takes new tasks (on/off), runs per day, max turns and minutes per run, model, tags; and its
  instructions, permissions and latest report (open in the editor). Changes are written into the role's
  `role.toml` at once, comments kept; the runner rereads it before its next task.
- **Claude Code**: installed and newest version, Update now (`claude update`), the agent systems the chat column's
  New chat offers, and the MCP servers every agent gets.

## Use
`odd1-agents` (or **Agent Settings** on the desktop and in the Odd1 menu) opens the window; `odd1-agents status [--json]` prints the overview
for scripts and agents. Agents can also operate the window itself: GTK 4 is accessible over AT-SPI (the `screen`
component). It has no MCP server of its own: its changes are the task manager's (`tasks` MCP) and role files.

## How it works
`odd1_agents/model.py` reads and writes everything as the person: the task store through `odd1_tasks` (its rules
apply), role settings by editing `role.toml` lines in place after checking each value (bounds in `LIMITS`), the
caretaker's `health.json`/`latest.json`, `/etc/odd1/agents.json` and `/etc/odd1/mcp-servers.json`.
`odd1_agents/app.py` (GTK 4, libadwaita) only shows the model, refreshing every 5 s; open task rows stay open.

## Tests
`python -m pytest components/agents/tests -q` (on Odd1: `odd1-agents-test`): the model offline, against a temporary
task store with both roles. The window was checked by hand on the agent desktop (`DISPLAY=:1 odd1-agents`).

## Next
- Pages for other agent systems as they arrive (each registers in `odd1.agents`; a page could come with it).
- Token budgets in dollars per role, once runs' costs (already logged per run) have a cap to enforce.
