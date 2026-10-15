# desktop
The person's desktop on any X display: Xfce's window manager and panel, themed, with the Claude chat docked on the right and everything else behind one menu.
Milestone: M4 (agent-native OS): the user-facing assistant layer; the person talks to the OS through the docked chat.

## Status
Packages only (xfwm4, xfce4-panel, whisker menu, xfce4-terminal, Colloid theme and icons, Inter, JetBrains Mono).
The session (units, xfconf defaults, chat dock, wallpaper) is being designed on a scratch display and switches
on together with Remote Desktop.

## Tests
`python -m pytest components/desktop/tests -q`: the module installs what the design needs.
