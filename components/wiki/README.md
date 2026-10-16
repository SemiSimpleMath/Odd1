# wiki
Search and read Odd1's wiki: the agents' knowledge base and the person's rules (wiki/ in the repository).
Milestone: M4 (agent-native OS): what agents learn stays learned; the rules every agent works by, in the person's words.

## Use
- `odd1-wiki search TEXT` (MCP `wiki_search`): sections matching all the words, active rules first, superseded ones
  last and marked; `odd1-wiki show PAGE` (`wiki_page`); `odd1-wiki rules [--all]`.
- Pages: `wiki/*.md`, short, linking to component READMEs instead of copying them. Any agent fixes a page that
  misled it (when it files the friction). Decision records: `wiki/decisions/NNNN-slug.md` with front matter
  (title, status active|superseded, superseded_by, ticket, date, decided_by) and the person's own words; only the
  person changes them: odd1-tasks-land refuses a branch touching wiki/decisions/ unless a person approved its task.
- `tools/check.py` flags a wiki page naming an `odd1-...` command or a repository path that does not exist.

## Tests
`odd1-wiki-test` (or `PYTHONPATH=components/wiki python -m pytest components/wiki/tests`): ranking (active first,
superseded last), paths kept inside the wiki, and that the repository's wiki holds the person's rules.
