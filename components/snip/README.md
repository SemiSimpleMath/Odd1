# snip
A snipping tool like Windows': Print or Super+Shift+S freezes the screen, the person snips a rectangle, a freeform shape, a window or the whole screen, and it is copied to the clipboard, saved and opened for marking up.
Milestone: M4 (agent-native OS): the user-facing layer; what a person expects from a desktop works here as on Windows.

## Use
- **Print** (or Shift+Print, or Super+Shift+S) snips; **Alt+Print** starts in Window mode. **Snipping Tool** in the
  menu does the same. Over Remote Desktop, Windows keeps Super+Shift+S for itself: use Print there.
- The overlay: the frozen screen, dimmed, with Rectangle, Freeform, Window, Full screen and x at the top. Drag (or
  click a window) to snip; Esc, x or a right click cancels.
- The snip is on the clipboard at once (Ctrl+V into a chat, or into Windows through Remote Desktop), saved as
  `~/Pictures/Screenshots/Snip YYYY-MM-DD HHMMSS.png`, and opened in the Snip window: **Pen** (P, colour button),
  **Highlighter** (H), **Crop** (C, drag the part to keep), **Undo** (Ctrl+Z), **Copy** (Ctrl+C), **Save** (Ctrl+S,
  over the saved snip), **Save as** (Ctrl+Shift+S, PNG or JPEG), **New** (Ctrl+N). Every change is copied as
  it is made.
- `odd1-snip [--mode rect|free|window|full] [--delay SECONDS] [--no-edit]` (prints the saved file's path).
- Agents don't need it: they see windows with `app_screenshot` (any-app) and the screen with `screen_view`. So snip
  has no MCP server.

## How it works
- `overlay.py` (GTK 3): the root window's image, taken before anything is drawn, on an override-redirect window
  covering the screen. It takes the keyboard and pointer while it is up, since the person asked for it.
  `windows.py` reads `_NET_CLIENT_LIST_STACKING` (python-xlib) for Window mode, frames included.
- `editor.py`: the snip as a cairo surface plus strokes. A crop flattens them. Undo takes back strokes and crops.
- `cli.py`: after the window closes, the process stays (hidden) while it still owns the clipboard: X keeps no copy
  of a selection once its owner exits. It offers PNG and BMP, which xrdp-chansrv hands to Windows.
- Keys: `module.nix` sets them in Xfce's `xfce4-keyboard-shortcuts` channel at session start (unit
  `odd1-snip-keys`), replacing Xfce's defaults, which call xfce4-screenshooter (not installed).
- Pure rules (rectangles, window under the pointer, file names) live in `shapes.py`.

## Tests
`python -m pytest components/snip/tests -q` (on Odd1: `odd1-snip-test`): the rules and the key bindings. Live
checks were done on the agent desktop (`DISPLAY=:1`): every mode, pen, highlighter, crop, undo, the clipboard's
image types, and the process exiting once another program took the clipboard.

## Next
- A delay menu in the overlay (3, 5, 10 s), as Windows has; today only `--delay`.
- Screen recording (Windows' snipping tool records video too).
