# Troubleshooting
- A permission was refused in a role's run: that is friction; file a ticket (the dojo also collects these daily).
- A deploy failed with `odd1-claude-code-install` exit 127: Claude Code was updating itself during the switch; run
  `odd1-deploy` again (ticket #19 is the fix).
- `tools/check.py` lists `__pycache__`: it checks only what git would commit; add the path to `.gitignore` if it is
  a new kind of leftover.
- Something deleted by accident: `odd1-backup list`, `odd1-backup restore-file PATH --at TIME` (home) or
  `odd1-backup restore DATE --to DIR` (Odd1's state). Nothing is overwritten: what is in the way moves aside.
- The disk fills: Nix collects garbage daily and under 2 GB free; `df -h /`.
