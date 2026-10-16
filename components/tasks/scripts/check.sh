#!/usr/bin/env bash
# odd1-tasks-check: the check of a task's branch before it lands: the repository's fitness check, the whole system
# built from the branch, and the tests of every component the branch changes (and of each COMPONENT named), run from
# that build (the branch's code, installed as Odd1 installs it).
#   odd1-tasks-check [COMPONENT...]      (in the task's worktree, $ODD1_TASKS_WORKTREE)
set -euo pipefail
wt=${ODD1_TASKS_WORKTREE:-$PWD}
cd "$wt"
python3 tools/check.py
out="$wt.build"   # beside the worktree, not in it
nix-build '<nixpkgs/nixos>' -I nixos-config="$wt/$(odd1-deploy host)" -A system -o "$out" >/dev/null
echo "system builds from $(git rev-parse --short HEAD)"
changed=$( { git diff --name-only main...HEAD; git diff --name-only HEAD; git ls-files --others --exclude-standard; } \
  | grep -o '^components/[^/]*' | cut -d/ -f2 || true)
for c in $(printf '%s\n' $changed "$@" | sort -u); do
  t="$out/sw/bin/odd1-$c-test"
  if [ -x "$t" ]; then echo "== odd1-$c-test"; "$t" -q; else echo "== $c has no odd1-$c-test"; fi
done
echo "check passed"
