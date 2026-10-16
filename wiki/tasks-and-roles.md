# Tasks, roles and routines
- A task has a goal, a done_when check (a command; exit 0 = done), tags and a log. Roles (`odd1-tasks-runner@ROLE`)
  claim tasks whose tags they hold and run Claude Code on them; the runner runs the check, the agent's word does not
  count. `odd1-tasks status`, `odd1-tasks attention`, Agent Settings in the menu.
- Who decides: the manager role approves within the tags the person delegated (`decides` in its role.toml) and
  escalates the rest to the person. Never approve your own proposal.
- Friction is a ticket: `odd1-tasks create "<what should work> (<what happened>)" --done-when '<check>' --tag friction
  --tag <component>`. The fixer works approved ones.
- Work done in a chat closes by its check: `odd1-tasks close-by-check ID` (rule
  [0009](decisions/0009-chats-do-not-claim-tickets.md)).
- Routines (the cron manager): pre-planned work on a schedule, each a folder with a skill
  (`routines/`): the journal (`~/Journal`), the dojo (reviews runs; OS faults become tickets, skill
  faults become proposed skill changes). A worker role runs them.
- Details: `components/tasks/README.md`.
