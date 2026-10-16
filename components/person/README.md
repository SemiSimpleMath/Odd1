# person
The person's own settings (About you): their name, what agents call them, pronouns, language, email and anything agents should know, read by every Claude Code session when it starts.
Milestone: M4 (agent-native OS): the user-facing assistant layer; agents that know who they work for.

## Use
- **About you** in the Odd1 menu (or the name at the top of the menu; `odd1-person settings`): one field per
  setting, Save. New conversations know the changes; ones already open do not.
- `odd1-person show | get KEY | set KEY VALUE | context`: the same for scripts and agents (`get call_me` gives the
  name to address the person by: their own choice, else their first name). An agent asked "call me X" runs
  `odd1-person set call_me X`.

## How it works
- `profile.py`: the installed settings (`/etc/odd1/person.json`, from `odd1.person` in `nix/odd1.nix`) overridden by
  the person's own (`~/.config/odd1/person.json`, only what differs). Saving a name or email also sets them in
  `~/.gitconfig`, so commits made here carry them.
- Claude Code's managed settings run `odd1-person context` as a SessionStart hook: every session, chat or role,
  starts with a few lines about the person (pronouns not given: they/them).
- `app.py`: the window (GTK 4, libadwaita). The system's user record carries the name (`users.users.agent.description`),
  so the menu's header shows it.

## Tests
`python -m pytest components/person/tests -q` (on Odd1: `odd1-person-test`): layering, saving, the session text,
the CLI. The window was checked on the agent desktop (`DISPLAY=:1 odd1-person settings`).

## Next
- A picture (`~/.face`, the menu shows it), the person's working hours and how they like to be reached.
- More settings as they come up: each goes here, not into a component of its own.
