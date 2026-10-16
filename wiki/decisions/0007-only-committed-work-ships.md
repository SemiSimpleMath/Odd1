---
title: Only committed, approved work ships
status: active
superseded_by:
ticket: #33
date: 2026-10-15
decided_by: the person (Jukka)
---
# Only committed, approved work ships

The system runs a clean, read-only release of one commit on main (odd1-deploy), never the development folder.
Deploys run one at a time, prove themselves (units, tests) or roll back on their own. The one override is the
person's, at a terminal, logged (odd1-deploy --force).
