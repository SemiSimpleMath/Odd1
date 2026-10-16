#!/usr/bin/env bash
# odd1-tasks-land: land a task's branch: rebase it on main, check it again (odd1-tasks-check, with the same COMPONENT
# names), fast-forward main to it, and deploy exactly that commit (odd1-deploy: a clean release, never the shared
# working tree, which may hold other sessions' edits). Refuses while the role's role.toml says review = true.
#   odd1-tasks-land [COMPONENT...]      (in the task's worktree, with everything committed)
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
# Decision records (wiki/decisions/) are the person's: they land only with a person's approval of this task.
odd1-tasks may-land "${ODD1_TASKS_TASK:?run it from a task}" $(git diff --name-only main...HEAD) || exit 4
odd1-tasks-check "$@"
branch=$(git rev-parse --abbrev-ref HEAD)
# Changes nothing and fails if main moved meanwhile or a local edit in the shared checkout is in the way.
git -C "$repo" merge --ff-only "$branch"
landed=$(git rev-parse HEAD)
status=0
odd1-deploy "$landed" --reason "land $branch" || status=$?
if [ "$status" != 0 ]; then echo "main is at $(git rev-parse --short HEAD) but the deploy failed ($status)" >&2; exit "$status"; fi
echo "landed $(git rev-parse --short HEAD) on main; the system runs it"
