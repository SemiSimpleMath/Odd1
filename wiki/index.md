# Odd1 wiki
The agents' knowledge base: how Odd1 works and the rules it runs by. Search it: `odd1-wiki search TEXT` (MCP
`wiki_search`). Explanatory pages are fixed by any agent that finds one wrong (fix the page that misled you when you
file friction); decision records (`decisions/`) are the person's: changes to them land only with the person's approval.

## Pages
- [How a change reaches the system](how-a-change-ships.md)
- [Tasks, roles and routines](tasks-and-roles.md)
- [The desktop and chats](desktop-and-chats.md)
- [Pods and secrets](pods-and-secrets.md)
- [Troubleshooting](troubleshooting.md)

## Rules and decisions
Every record in `decisions/` has a status: `active` (in force) or `superseded` (replaced; `superseded_by` names the
record that replaced it). Search shows active records first. Start with
[0001: the OS molds to the agents](decisions/0001-the-os-molds-to-the-agents.md) and the four rules (0002 to 0005).
