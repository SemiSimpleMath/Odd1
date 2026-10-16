#!/usr/bin/env bash
# The fixer's landing: rebase the task's branch on main, check it again, fast-forward main to it, and switch the system
# from a clean checkout of main (never from the shared working tree, which may hold other sessions' edits).
# Refuses while role.toml says review = true: then the branch goes to review by other agents instead.
#   /var/lib/odd1/tasks/roles/fixer/land.sh      (in the task's worktree, with everything committed)
set -euo pipefail
wt=${ODD1_TASKS_WORKTREE:?run it from the fixer task}
role=${ODD1_TASKS_ROLE_FOLDER:?run it from the fixer task}
repo=${ODD1_REPO:-/srv/odd1}
if grep -Eq '^review[[:space:]]*=[[:space:]]*true' "$role/role.toml"; then
  echo "review = true: this branch goes to review, not straight to main" >&2; exit 3
fi
exec 9>"$repo/.git/odd1-land.lock"; flock 9   # one landing at a time
cd "$wt"
[ -z "$(git status --porcelain)" ] || { echo "commit your work first (git status is not clean)" >&2; exit 2; }
git rebase main
"$role/check.sh"
branch=$(git rev-parse --abbrev-ref HEAD)
# Changes nothing and fails if main moved meanwhile or a local edit in the shared checkout is in the way.
git -C "$repo" merge --ff-only "$branch"
clean=$(mktemp -d "${TMPDIR:-/tmp}/odd1-land.XXXXXX")
git -C "$repo" worktree add --detach "$clean" main >/dev/null
host=$(grep -o '/srv/odd1/[^ ;]*\.nix' /etc/nixos/configuration.nix | head -1)
status=0
sudo -n nixos-rebuild switch -I nixos-config="$clean/${host#/srv/odd1/}" || status=$?
git -C "$repo" worktree remove --force "$clean"
if [ "$status" != 0 ]; then echo "main is at $(git rev-parse --short HEAD) but the switch failed ($status)" >&2; exit "$status"; fi
echo "landed $(git rev-parse --short HEAD) on main; the system runs it"
