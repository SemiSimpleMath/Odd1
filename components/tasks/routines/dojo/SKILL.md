---
name: dojo
description: Review the agents' recent runs, sort each stumble into an OS fault (file a friction ticket) or a skill fault (propose a skill change), and write the day's dojo report. Use when a task asks for the dojo.
---

# The dojo

Agents get better two ways: the OS stops getting in their way, and their skills get sharper. Which one a stumble
calls for matters most: never teach a skill to work around something the OS should do.

1. Gather: `python3 dojo-material.py` (the last day; `--days 3` for more). Per role and routine: runs, turns, cost,
   check results, and stumbles: DENIED (a permission refused the command), ERROR (a tool failed), REPEATED (the
   same call three times or more).
2. Read the previous report in `reports/` so you don't file the same thing twice. Check `tasks_list` for open
   tickets on the same problem.
3. Sort each stumble that cost turns:
   - **OS fault**: a permission that blocked ordinary work, a tool that failed or returned garbage, a missing
     capability, something slow, a wrong instruction in CLAUDE.md, a README or the wiki. File one friction ticket per
     problem with `tasks_create`: goal "<what should work> (<what happened, with task ids>)", a done_when that fails
     now and passes once fixed, tags `friction` and the component. Many denials of the same kind are one ticket.
   - **Skill fault**: the skill or role instructions were unclear, missed a step, had the wrong order, or made the
     agent re-read what it was told. Propose the change as a ticket tagged `friction` and `tasks` (or the routine's
     component): the exact text to change in which SKILL.md or CLAUDE.md, and why, quoting the run. Do not edit
     skills yourself: a proposal is reviewed and kept only if later runs pass in fewer turns.
   - **Unclear**: file it as an OS fault. Never write a workaround into a skill; if one is truly needed until a fix
     lands, the proposal says "temporary, remove when #N closes".
   - Nothing to learn (a run that went well): say so in one line.
4. Write `reports/YYYY-MM-DD.md` (today, `date +%F`): a table per role or routine (runs, turns, cost, checks), then
   the tickets you filed and the skill changes you proposed, each with its evidence (task id, the denied command or
   the error, shortened). Keep it under a page.

Rules: the rules of Odd1 hold (the person first; their data recoverable; nothing unlawful or unethical; no chaos).
Never propose a change to a rule record (wiki/decisions); that is the person's. Quote commands, never secrets.
