#!/usr/bin/env bash
# odd1-chat-land: land a chat's worktree branch (chat/<session>, odd1_agents/worktree.py) as roles land theirs: rebase
# it on main, check it (odd1-tasks-check: the repository check, the whole system built from the branch, the tests),
# fast-forward main to it and deploy exactly that commit (odd1-deploy).
#   odd1-chat-land [COMPONENT...]      (in the chat's worktree, with everything committed)
set -euo pipefail
repo=${ODD1_REPO:-/srv/odd1}
branch=$(git rev-parse --abbrev-ref HEAD)
case "$branch" in chat/*) ;; *) echo "run it in a chat's worktree (branch chat/...), not on $branch" >&2; exit 2;; esac
[ -z "$(git status --porcelain)" ] || { echo "commit your work first (git status is not clean)" >&2; exit 2; }
exec 9>"$repo/.git/odd1-land.lock"; flock 9   # one landing at a time, roles' included
git rebase main
odd1-tasks-check "$@"
# Changes nothing and fails if main moved meanwhile or a local edit in the shared checkout is in the way.
git -C "$repo" merge --ff-only "$branch"
odd1-deploy "$(git rev-parse HEAD)" --reason "land $branch"
echo "landed $(git rev-parse --short HEAD) on main; the system runs it"
