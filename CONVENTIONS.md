# Conventions

Few rules, enforced by `tools/check.py`. They exist so Odd1 can grow fast without becoming spaghetti.

## One place for everything
- One component per directory under `components/`, named in lowercase-with-dashes. That one name is used everywhere:
  directory, MCP server `<name>`, and with the `odd1-` prefix for commands and systemd units (`odd1-<name>`,
  `odd1-<name>-mcp`, `odd1-<name>-test`), so they never clash with real programs. No second name.
- One component per job. Before adding one, check ARCHITECTURE.md; if something similar exists, extend it.
- Runtime state lives on the VM under `/var/lib/odd1/<component>/`, never in this repository.

## What a component holds
```
components/<name>/
  README.md        what it does, who uses it, its interface (CLI, HTTP, files), how to run its tests
  <source files>
  tests/           at least one test that runs offline: `python -m pytest components/<name>/tests` (or test.sh)
  module.nix       optional: how it is installed or run on a VM (package, systemd unit)
```
The README's first line is a one-sentence purpose; the next names the ROADMAP.md milestone it serves (M1-M4 or ★). `tools/check.py` puts both on the component map. A component that serves no milestone has to justify itself.

## Agent entry point
A component that lets agents drive a program or use a capability exposes it as an MCP server (the standard
agent-to-software protocol) plus a CLI for people and scripts. Register the server in its module.nix as
`odd1.mcpServers.<name> = { command = ...; args = [ ]; };`; Odd1 lists it in /etc/odd1/mcp-servers.json and
registers it with Claude Code, so every agent on the machine can use it. Tools are named `<program>_<verb>`
(e.g. `calc_read`), take plain JSON arguments and return JSON. A friendly program gets a thin adapter over its own
interface; an unfriendly one gets an adapter built from the deeper layers (accessibility, draw calls, vision), but
agents see the same kind of entry point.

## Language
Python by default for components and adapters. Nix for the system, Bash for small glue, C or Rust only where
required (LD_PRELOAD hooks, eBPF, drivers; Rust preferred for new low-level code). Another language needs a reason.

## How things get onto a VM
Only from a commit of this repository, through `nix/odd1.nix` (which imports each component's `module.nix`), applied
with `nixos-rebuild switch`. No hand-copied files, no `/tmp` staging, no edits that exist only on the VM. If you
experiment on the VM, bring the result back here and commit it.

## What does not go in
Transcripts, conversation logs, screenshots, run outputs, caches, virtualenvs, build outputs, secrets of any kind.
Notes and plans stay short: a plan longer than a page is a sign the work should be split.

## Size and duplication
`tools/check.py` flags a component over 3,000 lines of source, two components providing the same systemd unit or
command, and missing READMEs or tests. A flag is a prompt to split, merge or delete, not to suppress the check.
