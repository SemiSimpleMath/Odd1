# desktop
The person's desktop on any X display: Xfce's window manager and panel, themed, with the chat column on the right and everything else behind one menu.
Milestone: M4 (agent-native OS): the user-facing assistant layer; the person talks to the OS through the chat column.

## Design
- Top bar (dark, 34px): the **Odd1** menu (apps, search, settings) on the left, open windows, the date and time.
- Chat column (right, a third of the screen, 460-680px): a tab per chat and **+ New chat**; the chat itself is a
  terminal running Claude Code (`$ODD1_CHAT_COMMAND` for any other agent harness). Apps can't cover it, maximized
  windows stop at it, and new windows open beside it.
- Look: Colloid (GTK, window frames, icons), Inter for the interface, JetBrains Mono in the chat, a calm light
  gradient, xfwm4's compositor for soft shadows. Grayscale antialiasing, so text stays crisp over Remote Desktop.
- The screen follows the viewer: Remote Desktop (xrdp, port 3389) and noVNC (6080) ask Xvnc for their window's
  size; the shell lays everything out again.

## Use
- Switch on: `odd1.desktop.enable = true;` in a host profile (base.nix then runs Xvnc, xrdp and this session
  instead of Xvfb, openbox and x11vnc). Connect with Windows' Remote Desktop to the machine's port 3389.
- `odd1-desktop new-chat [DIR]`, `odd1-desktop focus-chat`, `odd1-desktop shell --display :N` (the session runs it).
- Session units (user): `odd1-desktop-settings` (xfsettingsd), `-wm` (xfwm4), `-panel`, `-shell`.

## How it works
`odd1_desktop/shell.py` turns every window titled "Claude Code" into a dock window under its header (docks are
never covered and never constrained), puts the EWMH strut that reserves the column on the header, focuses the shown
chat on clicks in the column (window managers do not focus docks; XInput 2 raw button events), moves new app
windows out of the column once, and repaints the wallpaper on every resize (RandR). Pure rules live in `layout.py`.
xfwm4's own margins could not do this: it caps them at 320px.

## Tests
`python -m pytest components/desktop/tests -q` (layout rules, module wiring; offline). On Odd1: `odd1-desktop-test`.
Live checks were done on a scratch Xvnc display with a FreeRDP client: a 1600x900 client resized the screen to
1600x900, docking, tabs and new chats worked, typing reached the docked chat.

## Next
- Authentication before shipping an image: VNC is localhost-only and xrdp passes no password, which is fine behind
  the dev VM's port forward but not on a network. First boot should set a password (VncAuth or PAM in xrdp).
- Clipboard over VNC is ISO-8859-1 text only (xrdp's VNC module); a native xorgxrdp session would lift that.
- A keyboard shortcut for the chat (Super+Space) and for the menu (Super).
