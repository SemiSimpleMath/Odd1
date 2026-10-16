# desktop
The person's desktop on any X display: Xfce's window manager and panel, themed, with the chat column on the right and everything else behind one menu.
Milestone: M4 (agent-native OS): the user-facing assistant layer; the person talks to the OS through the chat column.

## Design
- Top bar (dark, 34px) over the work area only: the **Odd1** menu (apps, search, settings) on the left; on the
  right, at the work area's corner beside the chat column, the maximized window's minimize/maximize/close buttons
  and the date and time. A maximized window has no title bar of its own (xfwm4 `titleless_maximize`): its title
  follows the menu and its buttons sit in the bar (xfce4-windowck-plugin). Other windows keep a dark title bar.
- Desktop: a dark wallpaper (navy lit by soft colour fields, drawn per screen size, the Odd1 owl dimmed into its
  middle) and launcher icons from
  `~/Desktop` (xfdesktop). Files, Firefox, Terminal, LibreOffice, Text Editor and Calculator are put there once.
- Taskbar (floating, bottom of the work area): what is running. One item per app of the person's; **one Agents
  item** for everything agents run on the agent desktop (badge: how many windows; click shows or hides it); one
  item per chat in the column. Hover names an item; clicking an app brings it forward or, if focused, minimizes it.
  The rule is the display: windows on the person's screen are the person's, windows on :1 are agents'.
- Chat column (right, a third of the screen, 460-680px, full height): a tab per chat and **+ New chat** at the top; the chat itself is a
  terminal running Claude Code (`$ODD1_CHAT_COMMAND` for any other agent harness). Apps can't cover it, maximized
  windows stop at it, and new windows open beside it.
- Remote Desktop carries the clipboard both ways, images included (paste a screenshot into a chat with Ctrl+V),
  and the drives the viewer shares (Remote Desktop: Local Resources, More, Drives) appear at `~/Windows`, so
  agents can read a file the person put in their Downloads. Both through `xrdp-chansrv` (unit
  `odd1-desktop-clipboard`).
- Quiet: the X bell is off (the shell sets its volume to 0), so agents typing into apps make no beeps over Remote
  Desktop.
- Logo: `logo/odd1-logo.svg` (the owl and the wordmark, its "1" in orange); `logo/odd1-icon.svg` is the owl
  alone, installed as the icon `odd1` (the menu button). Traced from the person's artwork with potrace, one layer
  per colour.
- Look: Colloid (GTK and icons light; window frames are the `Odd1` xfwm4 theme, Colloid-Dark with its colours
  fixed to the bar's, since xfwm4 otherwise takes them from the light GTK theme; its `unity/` holds the same
  buttons for the top bar), Inter for the interface, JetBrains Mono in the chat, xfwm4's
  compositor for soft shadows. Grayscale antialiasing, so text stays crisp over Remote Desktop.
- The screen follows the viewer: Remote Desktop (xrdp, port 3389) and noVNC (6080) ask Xvnc for their window's
  size; the shell lays everything out again.

## Use
- Switch on: `odd1.desktop.enable = true;` in a host profile (base.nix then runs Xvnc, xrdp and this session
  instead of Xvfb, openbox and x11vnc). Connect with Windows' Remote Desktop to the machine's port 3389.
- `odd1-desktop new-chat [DIR]`, `odd1-desktop focus-chat`, `odd1-desktop shell --display :N` (the session runs it).
- Session units (user): `odd1-desktop-settings` (xfsettingsd), `-wm` (xfwm4), `-panel`, `-icons` (xfdesktop),
  `-shell`. Restarting the shell keeps chats open (KillMode=process; new chats run in their own scope).
- Settings: Odd1 owns the Xfce channels in `xfconf/`. At session start their copies in `~/.config/xfce4` are
  removed, so the repository's values apply (change them here, not in Xfce's settings dialogs). dconf carries
  the same theme and fonts, or xfsettingsd copies GNOME's defaults (Adwaita) over them.

## How it works
`odd1_desktop/taskbar.py` draws the taskbar (window icons from `_NET_WM_ICON`, the Agents and chat icons drawn
here, translucency faked over the wallpaper it covers) and counts the agent desktop's windows every 2s.
`odd1_desktop/shell.py` turns every window titled "Claude Code" into a dock window under its header (docks are
never covered and never constrained), puts the EWMH strut that reserves the column on the header, focuses the shown
chat on clicks in the column (window managers do not focus docks; XInput 2 raw button events), moves new app
windows out of the column once, and repaints the wallpaper on every resize (RandR). Pure rules live in `layout.py`.
xfwm4's own margins could not do this: it caps them at 320px. On every resize it also sets the panel's length
to the work area's width (`layout.panel_length`).

## Tests
`python -m pytest components/desktop/tests -q` (layout rules, module wiring; offline). On Odd1: `odd1-desktop-test`.
Live checks were done on a scratch Xvnc display with a FreeRDP client: a 1600x900 client resized the screen to
1600x900, docking, tabs and new chats worked, typing reached the docked chat.

## Next
- Authentication before shipping an image: VNC is localhost-only and xrdp passes no password, which is fine behind
  the dev VM's port forward but not on a network. First boot should set a password (VncAuth or PAM in xrdp).
- A keyboard shortcut for the chat (Super+Space) and for the menu (Super).
- The libreoffice MCP opens documents on the person's screen (:0); it should use the agent desktop, so they fold
  into the Agents item until handed over.
