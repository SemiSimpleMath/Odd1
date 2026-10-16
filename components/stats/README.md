# stats
Collect Odd1's agent and system stats every 15 minutes and show whether agent performance is degrading and where the friction is.
Milestone: M4 (agents that run the machine need to see how well they are doing).

## What it collects
`odd1-stats.timer` runs `odd1-stats sample` every 15 minutes (as `agent`); each run appends one JSON line to
`/var/lib/odd1/stats/samples.jsonl`. It only reads other components and keeps counts, never prompts, outputs or
transcript text:
- `tasks` (tasks.db, read-only): per UTC day and role, runs, outcomes (done, retry, parked, blocked), done after a
  retry, budget hits, turns, cost, minutes; friction tickets opened and closed per component (the ticket's other
  tags). Days since the previous sample are recomputed each time; the latest sample of a day wins.
- Claude Code transcripts (`~/.claude/projects/*/*.jsonl`), read from a byte offset kept in `cursor.json`: per
  session and since the last sample, turns, tokens (in, out, cache), tool calls and errors per tool, permission
  denials, interrupts. Source `routine` is a task run (project under /var/lib/odd1/tasks), `chat` the rest.
- `caretaker` (history.jsonl, latest snapshot): PSI avg60, memory available, fullest disk, failed units.
- `deploy` (deployments.jsonl, new lines): switches, failures, rollbacks.

At the first sample of a new month, `samples.jsonl` becomes `samples-YYYY-MM.jsonl`; reports read all of them.

## Interface
- `odd1-stats summary [--days N] [--json]`: tasks by role (with turns and cost per done task), usage by source,
  tool errors by tool and MCP server, friction tickets by component, the machine, deploys.
- `odd1-stats trend [--days N] [--json]`: the last N days against the N before: success rate, turns, cost and
  minutes per done task, budget hits per run, tool-error rate (overall and per tool with 10+ calls), denials and
  interrupts per session, friction tickets. `WORSE` = 20% or more worse with enough data on both sides.
- `odd1-stats sample`: one sample now.
- MCP `stats`: `stats_summary(days)`, `stats_trend(days)` (the same, as JSON).

Usage is the limiting resource (the Claude Max plan), so it is tracked (turns, tokens, cost per role and source);
there is no cap.

## Tests
`python -m pytest components/stats/tests` (offline, fixture tasks.db, transcripts and histories), or `odd1-stats-test`.
