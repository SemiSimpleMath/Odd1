# Odd1

An operating system built for agents: the least possible friction for an agent to operate the computer and every
program installed on it. Agents can pip install anything, take libraries apart, build drivers, and make their own
tools whenever a tool makes the next task easier. People mostly make requests; mouse and keyboard work as usual.

Odd1 runs as NixOS virtual machines (the Desktop, with a graphical session and an app collection, and the Machine).
Nothing in Odd1 touches the host computer.

## This repository is the source of truth

Everything Odd1 runs comes from here, at a commit. Code that exists only on a VM, in /tmp, or in a session
workspace does not count and will be lost. If it matters, it lives here.

- `components/<name>/`: one directory per component (a service, an app interface, a tool, a library). See
  CONVENTIONS.md for what a component holds.
- `nix/`: how components are installed on the VMs. `nix/odd1.nix` is the Odd1 module.
- `ARCHITECTURE.md`: the map of components and how they connect. Keep it current.
- `ROADMAP.md`: the milestones (Caretaker, Optimizer, Kernel, Agent-native OS, any app). Every component serves one.
- `CLAUDE.md`: what an agent working on Odd1 needs to know (installed on the VMs for Claude Code).
- `tools/check.py`: the fitness check. Run it before you commit; it must pass.

## Growing Odd1

Ask the key question first: why is this hard, and could a change to the OS or to your tools make a whole class of
such work much easier? Then build the smallest thing that does it, as a component, with a test. Prefer bash and
existing programs; write a tool when the work is complicated and clearly recurring. Extend an existing component
before adding a new one that overlaps it.
