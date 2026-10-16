# backup
Local backups of Odd1's state and the person's files, so no accident destroys the person's data (rule 2).
Milestone: M1 (the machine is recoverable: undo for data, beside rollback for the system).

## Use
- `odd1-backup run [state|home]` (timers do it: state daily at 03:30, home hourly), `odd1-backup list`.
- `odd1-backup restore DATE [--to DIR]`: Odd1's state as it was that day (default target /var/lib/odd1: stop the
  services first). `odd1-backup restore-file PATH [--at 2026-10-16T09:00]`: a file or folder of the home folder as
  it was then (default: the latest snapshot). A restore never deletes: what is in the way moves to
  `PATH.before-restore-<time>`.

## How
- `/var/backups/odd1/<YYYY-MM-DD>/`: each component's SQLite database as a consistent online backup named after the
  component (`tasks.db`), everything else of /var/lib/odd1 under `files/`; deploy releases (rebuilt from git) and the
  secret store (keeps its own encrypted backups) are left out. 14 days kept.
- `/var/backups/odd1/home/<YYYY-MM-DDTHH-MM>/`: rsync with `--link-dest`, so unchanged files are hard links and an
  hour costs only what changed. Left out: caches, npm and Nix downloads, `~/Windows` (the Remote Desktop drives,
  another computer's files), virtualenvs, node_modules. The last 48 snapshots kept, and each day's last for 14 days.
- Same disk as the data: this covers accidents, not a failed disk. The repository's off-machine copy is GitHub;
  an off-machine copy of these snapshots is the next step.

## Tests
`odd1-backup-test` (or `PYTHONPATH=components/backup python -m pytest components/backup/tests -q`): a live WAL
database copied consistently and opened by the real task store after a restore, home snapshots sharing unchanged
files and leaving out caches and remote drives, a deleted file brought back (the newer one kept aside), retention.
