# agent-desktop
A second X display where agents run apps out of the person's way: its own window manager, pointer and keyboard, shown as one minimized window on the person's screen that they can open to watch or take over.
Milestone: M4 (agent-native OS): agents as first-class users of the machine who never compete with the person for mouse, keyboard or focus.

## Use
- Agents: the MCP server `agent-desktop` (registered for every agent): `desktop_run(command)` starts a program
  there and returns `{unit, windows}`; `desktop_status()`, `desktop_stop(unit)`, `desktop_show()`,
  `desktop_hide()`. Read and drive its apps with the `screen` tools, `display="agent"` (ids `a1.e5`); the
  pointer and keyboard there are the agent desktop's own, so real clicks and typing never touch the person's.
- Command line: `odd1-agent-desktop run gnome-calculator`, `status`, `stop UNIT`, `show`, `hide`.
- The person: click "Agent desktop" in the taskbar to watch; mouse and keyboard inside that window work there.

## How it runs
User services `odd1-agent-desktop` (Xephyr `:1`, 1280x720 and resizeable: the display follows its window, and
`show` fits it to the person's work area, which excludes panels and the docked chat column; a window titled "Agent desktop" on `:0`, minimized at start) and `odd1-agent-desktop-wm` (openbox on `:1`). Programs run as transient user units
`odd1-agent-app-*` in `odd1-agent.slice` (CPU and IO weight 20 against the default 100, so they yield to the
person's programs; caretaker can freeze the slice), with `GNOME_ACCESSIBILITY=1` and `QT_ACCESSIBILITY=1`. They
share the session's D-Bus and AT-SPI bus, so `screen` sees their trees. Xephyr keeps rendering while its window
is minimized.

Not the same as `any-app-broker`: the broker makes short-lived, capability-gated sandboxes for an app an agent is
learning; this is the lasting desktop where agents do ordinary work.

## Tests
`python -m pytest components/agent-desktop/tests -q`: offline (how apps are started, new-window detection, stop
policy, MCP tools); the live test (a Tk window opens on `:1`, not `:0`, and the person's pointer stays put when
the agent's moves) skips unless the agent desktop runs. On Odd1: `odd1-agent-desktop-test`.

## Next
- Move a window between the agent desktop and the person's screen (hand a finished document over), e.g. by
  reopening the file there, or an xpra-style forwarder.
- Caretaker: freeze `odd1-agent.slice` while the person's foreground program is under pressure.
- More than one agent desktop (one per busy agent), if agents start to get in each other's way.
