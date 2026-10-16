---
title: The repository is the same for every install
status: active
superseded_by:
ticket: #66
date: 2026-10-15
decided_by: the person (Jukka)
---
# The repository is the same for every install

Odd1 will be open source: the repository holds what every user gets; one person's or one machine's values (name,
email, keys, addresses) live in the install's local settings (/srv/odd1-local), About you, or the secret store, never
in the shared repository. History is not rewritten unless something very private is found; the person's name in
commits is fine. Secrets never go into a chat, the repository, logs or memory: a person stores them (odd1-secrets).

In the person's words: "we don't want to put into github config file with my info already in it" and "i don't
want to scrub anything unless there is something very private."
