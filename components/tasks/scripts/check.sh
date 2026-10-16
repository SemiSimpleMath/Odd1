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
config=$(odd1-deploy config-for "$wt")
log=$(mktemp)
trap 'rm -f "$log"' EXIT
if ! nix-build '<nixpkgs/nixos>' -I nixos-config="$config" -A system -o "$out" >/dev/null 2>"$log"; then
  # Why it failed, not what it would have built: Nix's messages without its lists of paths to build or fetch, then
  # each failed derivation and the end of its build log.
  echo "system does not build from $(git rev-parse --short HEAD):" >&2
  grep -Ev "^(these [0-9]+|this) (derivations?|paths?) will be |^  /nix/store/|^building '|^copying path " "$log" \
    | tail -n 40 >&2 || true
  for drv in $(grep -oE "builder for '[^']+\.drv' failed" "$log" | grep -oE "/nix/store/[^']+\.drv" | sort -u); do
    echo "== failed: $drv (nix log $drv), its last lines:" >&2
    nix log "$drv" 2>/dev/null | tail -n 40 >&2 || true
  done
  exit 1
fi
echo "system builds from $(git rev-parse --short HEAD)"
changed=$( { git diff --name-only main...HEAD; git diff --name-only HEAD; git ls-files --others --exclude-standard; } \
  | grep -o '^components/[^/]*' | cut -d/ -f2 || true)
for c in $(printf '%s\n' $changed "$@" | sort -u); do
  t="$out/sw/bin/odd1-$c-test"
  if [ -x "$t" ]; then echo "== odd1-$c-test"; "$t" -q; else echo "== $c has no odd1-$c-test"; fi
done
echo "check passed"
