---
name: journal
description: Write the day's entry of the person's Odd1 journal (major decisions, tickets, shipped changes) from journal-sources.py's material. Use when a task asks for a journal entry.
---

# Writing a journal entry

1. Gather: `python3 journal-sources.py` (since where the last entry stopped; `--since 2026-10-15T08:00` to choose).
   It prints the task store's tickets and decisions, commits on main, deploys, and the person's own messages in their
   chats, each with the end of the answer it replied to. It is long: read all of it before writing.
2. Read the latest entry in `journal/` (if any) so the new one continues it: what was open then and is decided now.
3. Write `journal/YYYY-MM-DD.md` (today's date, `date +%F`). If it exists (a second run today), add to it.

## What goes in
Major only: a decision that changes how Odd1 works, what agents may do, or what the person wants; a ticket opened or
closed for something that mattered; a change shipped. Leave out routine runs (the manager's 15-minute rounds,
recurring reports that found nothing), retries and agents' working notes. Name things as the person would: "the
deploy tool", not "components/deploy/odd1_deploy/deploy.py".

```
# Journal, YYYY-MM-DD
(covers <since> to <until>)

## The person decided
- <decision, in one sentence> — <why, in their words if they gave one> (#ticket, commit)

## Agents decided
- <role>: <decision> (#ticket); <what the person was escalated, and whether they answered>

## Built and shipped
- <what now works> (commit, deployed or not)

## Tickets
- Opened: #n <short goal>, ...   Closed: #n done/declined/cancelled, ...

## Open
- <what waits, and on whom>
```

Rules:
- Every line traces to the material: a ticket id, a commit, or the time of the person's message. Never invent a
  reason; if none was given, write none.
- The person's words are theirs: quote short phrases, do not soften or strengthen them. A question is not a decision.
- Keep it under a page. Prefer one precise line to three vague ones.
- No secrets: drop tokens, passwords, keys or e-mail addresses that appear in the material.
