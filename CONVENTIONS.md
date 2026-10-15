# Conventions

Few rules, enforced by `tools/check.py`. They exist so Odd1 can grow fast without becoming spaghetti.

## One place for everything
- One component per directory under `components/`, named in lowercase-with-dashes. The name is used everywhere:
  directory, systemd unit, CLI command, notes. No second name.
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
The README's first line is a one-sentence purpose; `tools/check.py` puts it on the component map.

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
