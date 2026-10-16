# deploy
Put an approved commit of this repository on the machine, from a clean, isolated release, never from the development folder.
Milestone: M1 (the machine changes only through committed, checked work; deploys can be undone).

## Use
- `odd1-deploy [COMMIT]` (default `main`; the commit must be on main) builds and switches the system; `--boot`
  activates on next boot, `--build` only builds. `odd1-deploy rollback` deploys the commit current before.
  `odd1-deploy status [--json]` shows the deployed commit, how far main is ahead, the last deploys.
  `odd1-deploy host` prints the host file (e.g. `nix/hosts/dev-vm.nix`) for scripts that build a checkout.
- Agents: MCP server `deploy` (`deploy_status`, `deploy_commit`, `deploy_rollback`).
- `sudo nixos-rebuild switch` alone rebuilds the deployed release (the same commit), not `/srv/odd1`.

## How
- The commit is exported with `git archive` into `/var/lib/odd1/deploy/releases/<sha>` (read-only, `.odd1-release`
  names the commit) and built with `nixos-rebuild ACTION -I nixos-config=<release>/<host>`. Git is only read:
  the shared checkout's edits, index, stashes, branches and the task worktrees stay as they are, and none of it ships.
- After a successful switch, `current -> releases/<sha>` and `/etc/nixos/configuration.nix` imports
  `/var/lib/odd1/deploy/current/<host>` (written with `sudo -n install`). A failed build changes neither.
- One deploy at a time (`flock` on `lock`; `--no-wait` fails instead of waiting). Each is a line in
  `deployments.jsonl` (commit, action, ok, previous, actor, reason, system); the last 5 releases stay on disk.
- `odd1-tasks-land` merges a task branch into main and deploys it with `odd1-deploy`; `odd1-tasks-check` builds a
  worktree using `odd1-deploy host`.

## Tests
`odd1-deploy-test` (or `python -m pytest components/deploy/tests -q` with `PYTHONPATH=components/deploy`): a
throwaway repository and a fake nixos-rebuild prove that committed changes ship and uncommitted, staged, stashed,
untracked and task-worktree edits do not and stay untouched; that the configuration imports the release; that
off-main commits are refused, a failed switch keeps the running release, rollback works, deploys are serialized.
