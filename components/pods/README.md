# pods
Content passed between agents and tools by reference, with authority levels; secrets are pods agents hand on but never read.
Milestone: M4 (agent-native OS): the shared currency of agents' work; the base a personal assistant (Emi) can run on.

## Use
- Agents: MCP server `pods`: `pod_put` (content -> `pod:<kind>:<id>`), `pod_show` (header, no content), `pod_get`
  (a projection), `pod_find`, and the courier: `pod_http` (`{{pod:...}}` placeholders in headers or body, HTTPS) and
  `pod_run` (environment variables from pods). Pass references in messages, task logs and files, not content.
- `odd1-pods put|get|show|find|audit`; `odd1-secrets list|set|import|allow|run|http` (see `--help`).
- Keys for a service: `odd1-secrets import my.env --allow OPENAI_API_KEY=env:OPENAI_API_KEY` (or
  `set NAME --allow host:api.openai.com`, the value typed hidden), at a terminal. Then
  `odd1-secrets run --with OPENAI_API_KEY=pod:secret.token:... -- program`. Never paste keys into a chat.

## How
- Borrowed from Emi's data pods (the person's assistant; studied 2026-10-16): references, a kind registry
  (`kinds.json`; unknown kinds refused), authority levels (10 public, 50 agent, 99 person, 100 courier), projections
  (a secret's `last4` and `length` at 50, its value only at 100), the same answer for "missing" and "not allowed",
  and an append-only audit of every read and refusal (never values).
- Better than Emi's on the weak points found there: a secret goes only to destinations its pod lists (`host:`,
  `env:`, `field:`; Emi's courier sent any secret anywhere); every reply, output and error is scrubbed of the
  values (plain, base64, URL, hex); no "trusted" bypass; HTTPS only and no secrets in URLs; blob paths are hashes.
- Content ids are hashes of the content (one pod per content); secrets get random ids. Bytes live in `blobs/` by
  SHA-256. Secrets' values are in `/var/lib/odd1/secrets/pods.db` (0600), apart from everything else and left out of
  plain backups (an encrypted backup of them is still to come).
- Limits: every agent runs as the same user with sudo, so this stops accidents (keys in logs, transcripts, commits,
  context), not an agent set on reading the vault file. A program given a secret through `env:` can still reveal it
  in a transformed form; allow it only to programs trusted with it. For real isolation, API keys can be inserted by
  the host's egress proxy so they never enter the VM (a host-side follow-up).

## Tests
`odd1-pods-test` (also `odd1-secrets-test`; or `PYTHONPATH=components/pods python -m pytest components/pods/tests`):
references and kinds, a secret readable by no agent (and absent from the main database), the courier sending to an
allowed host and refusing others, replies and program output scrubbed, people-only storing and routing.

## Next
- `field:` destinations: typing a card number or password into a form (screen/browser), then hiding it in later
  screenshots and page text. Skills, reports and journal entries as pods; tasks pointing at their input and output pods.
