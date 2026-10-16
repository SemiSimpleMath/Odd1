# routines
Pre-planned work the cron manager (components/tasks, `odd1_tasks/cron.py`) runs on a schedule, each by whichever
worker agent is free. A routine is a folder: `routine.toml` (enabled, schedule, goal, done_when, budgets, `allow`:
the permission rules its run needs), `SKILL.md` (the skill that says how), and its helpers. Content, like the wiki:
add a folder to add a routine. Seeded to /var/lib/odd1/tasks/routines/ (the person's edits kept).
- `journal/`: the person's daily journal (`~/Journal`).
- `dojo/`: reviews the day's agent runs; OS faults become friction tickets, skill faults proposed skill changes.
