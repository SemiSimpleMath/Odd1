#!/usr/bin/env bash
# The fixer's check of its branch: the repository's fitness check, the whole system built from the branch, and the
# tests of every component the branch changes, run from that build (the branch's code, installed as Odd1 installs it).
#   /var/lib/odd1/tasks/roles/fixer/check.sh      (in the task's worktree, $ODD1_TASKS_WORKTREE)
set -euo pipefail
wt=${ODD1_TASKS_WORKTREE:-$PWD}
cd "$wt"
python3 tools/check.py
host=$(grep -o '/srv/odd1/[^ ;]*\.nix' /etc/nixos/configuration.nix | head -1)
out="$wt.build"   # beside the worktree, not in it
nix-build '<nixpkgs/nixos>' -I nixos-config="$wt/${host#/srv/odd1/}" -A system -o "$out" >/dev/null
echo "system builds from $(git rev-parse --short HEAD)"
changed=$( { git diff --name-only main...HEAD; git diff --name-only HEAD; git ls-files --others --exclude-standard; } \
  | grep -o '^components/[^/]*' | cut -d/ -f2 | sort -u || true)
for c in $changed; do
  t="$out/sw/bin/odd1-$c-test"
  if [ -x "$t" ]; then echo "== odd1-$c-test"; "$t" -q; else echo "== $c has no odd1-$c-test"; fi
done
echo "check passed"
