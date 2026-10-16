# publish
Push the Odd1 repository's main to its GitHub remote with the token from the vault: fast-forward only, checked for credentials first.
Milestone: ★ (the repository is the source of truth): the source of truth gets a copy off the machine, published by the person.

## Use
- Once: the person stores the token at a terminal (`odd1-secrets set GITHUB_TOKEN --allow env:GH_TOKEN --allow
  host:github.com --allow host:api.github.com`), a fine-grained token for the one repository with Contents: read
  and write; then `odd1-publish setup https://github.com/OWNER/REPO`.
- `odd1-publish` pushes main; `--dry-run` does everything but the push; `status` shows the remote, the token's pod
  and what a push would do. An agent runs it when the person asks it to push in a chat.
- The person's chats may run it (an allow rule for `odd1-publish` in the agent user's Claude Code settings); task
  roles are denied pushing and read only their role's settings.

## How it works
- The token goes to git only for the command, through `odd1-secrets run --with GH_TOKEN=pod:...` and a credential
  helper that reads the variable: never in a URL, git's config or a file, and scrubbed from git's output.
- Before pushing: the remote must be a plain `https://github.com/OWNER/REPO`; a remote main that is not in ours is
  refused (never forces); the added lines of every commit to send are checked for credential shapes (GitHub, AWS,
  Anthropic, OpenAI, Slack, Google keys, private keys), and one found stops the push (`rules.py`).
- No MCP server: publishing is rare and the person's; the CLI is the whole interface.

## Tests
`python -m pytest components/publish/tests -q` (on Odd1: `odd1-publish-test`): URL and credential rules, and pushes
to a local bare remote (create, up to date, a credential refused, a remote that moved on refused).
