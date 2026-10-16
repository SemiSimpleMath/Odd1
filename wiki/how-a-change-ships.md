# How a change reaches the system
1. Change the repository (`/srv/odd1`) in your own git worktree, never in the shared checkout: `odd1-tasks` gives
   fixer and updater tasks one, and a chat gets its own (`/var/lib/odd1/chats/<session>`, branch `chat/<session>`)
   the first time it edits the repository (components/agents). One component per folder under `components/`
   (CONVENTIONS.md).
2. Check: `python tools/check.py` (the repository's shape; it also refuses personal data, secrets and direct
   system rebuilds), and the component's tests. Roles run `odd1-tasks-check`, which builds the whole system.
3. Commit on your branch and land it: `odd1-tasks-land` (roles) or `odd1-chat-land` (chats) rebases, checks again,
   fast-forwards main and deploys.
4. Deploy: `odd1-deploy`. It builds a clean, read-only export of one commit on main plus this install's local
   settings (`/srv/odd1-local`), switches, then proves itself: no unit failed that was fine before, and the changed
   components' installed tests pass. Otherwise it rolls back on its own. `odd1-deploy status` says what runs;
   `odd1-deploy rollback` goes back one.
- Never activate a system built straight from `/srv/odd1` with nixos-rebuild: it is refused (the working tree holds other sessions' unfinished
  edits). The person's override is `odd1-deploy --force`, at a terminal, logged.
- Publishing to GitHub (https://github.com/SemiSimpleMath/Odd1, public) is the person's call: when they ask, run
  `odd1-publish` (main only, fast-forward, credential scan first, token from the vault). Never `git push` by hand:
  the token is not in git's config, and the person's chats are allowed `odd1-publish` alone. Task roles never push.
- Details: `components/deploy/README.md`, rule [0007](decisions/0007-only-committed-work-ships.md).
