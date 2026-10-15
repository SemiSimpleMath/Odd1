# screen
The whole desktop as one queryable model: X11 windows, AT-SPI trees and OCR text fused, in screen pixels, with stable ids, change events and verified click targets.
Milestone: ★ (any app, through interfaces agents write themselves). The screen-model service that per-app adapters build on.

## Use
- Agents: the MCP server `screen` (registered for every agent). One model per display, so ids stay stable:
  `display="user"` is the person's screen (windows `w1`, `w2`, ...), `display="agent"` the agent desktop
  (`a1`, ...; see `agent-desktop`). Actions follow the id's display.
  - `screen_view(window, ocr, include_hidden, max_elements)`: `{screen, windows: [{id: "w3", title, box, active,
    source, calibration, elements: [{id: "w3.e12", role, name, value?, box, states, verified?}]}]}`, topmost
    window first; every `box` is screen pixels `[x, y, width, height]`. `ocr`: `auto` (windows without a tree, or
    whose coordinate mapping is not yet established), `always` (re-verify), `never`.
  - `screen_changes(window)`: windows opened/closed/changed, elements added/removed/changed (values too) since
    the previous view.
  - `screen_at(x, y)`: the window and smallest element that own a pixel (stacking order decides).
  - `screen_find(text, role)`.
  - `screen_click(id, via)`: press an element and return what changed. `via="auto"` (default) asks the app itself
    through its accessibility action whenever the element has one: it works on minimized and covered windows and
    leaves the person's mouse alone. `"pointer"` uses the real mouse (for OCR-only apps), refused when the window is
    minimized, covered there, or the box is contradicted or unverified.
  - `screen_set_text(id, text)`: replace a field's text through the app (minimized or covered too; never password
    fields). Read back: an app that claims success but changes nothing is an error.
  - `screen_type(text, id)`: the real keyboard into the focused window (takes the person's focus; last resort).
- Command line: `odd1-screen [--display :1] view [WINDOW] [--ocr always]`, `odd1-screen at X Y`, `odd1-screen watch [WINDOW]`
  (one JSON line per change).
- Python: `PYTHONPATH=$ODD1_SCREEN_PYTHONPATH python3`, then `from odd1_screen import live, model`;
  `live.observe(model.Screen())`.

## How it decides where things are
X11 is the authority on window position and stacking; toolkits often misreport their own origin. Each window's
AT-SPI boxes are mapped to the screen by one of three mappings (toolkit desktop coordinates; X11 origin + window
coordinates; X11 origin + frame-relative), chosen by OCR evidence: the mapping that puts the most named elements
over their own text. An element is `verified: true` when OCR found its name in its box, `false` when OCR found it
only elsewhere (the toolkit misplaces it; the OCR `text` element shows where it really is). `screen_click` refuses
contradicted elements, unverified ones in unverified windows, and points another window covers. OCR reads each
window from an offscreen capture (any-app's `screenshot`, XComposite), so covered text is real; it is marked
`covered`. Windows without a tree (xterm, Tk) are OCR only.

## What was learned
- GTK4 (GNOME Calculator) reports desktop coordinates of 0,0 for everything; its window coordinates are right for
  the content but its header bar is off. Qt (FeatherPad) desktop coordinates match X11.
- Firefox publishes nothing over AT-SPI unless started with `GNOME_ACCESSIBILITY=1`; then its whole page (headings,
  buttons, entries) arrives with correct desktop coordinates. module.nix sets it for the session.
- Verified end to end: Calculator driven by element id (C 1 2 × 3 =), the display's value read back as `36`.
- Minimized windows are unmapped (no pixels, no place for a pointer), but their accessibility trees stay live:
  minimized Calculator computed 9 × 9 = 81 and minimized FeatherPad took text, both through the app itself.
- Firefox 146 accepts AT-SPI text writes into web form fields and applies none (focused or not); pressing works.
  Web forms belong to the `browser` component.

## Tests
`python -m pytest components/screen/tests -q` runs offline anywhere (synthetic desktops: mapping choice, stable
ids, occlusion, contradictions, change events, click refusals). `test_live_screen.py` reads a disposable Tk window
and skips without DISPLAY and the tools. On Odd1: `odd1-screen-test`.

## Next
- Hold a permanent offscreen redirect (XComposite) and listen to Damage events: instant captures of any mapped
  window (measured 2 ms) and change events for only the regions that changed.
- Feed the measurement: the ★ benchmark should drive apps through `screen_*` and count steps and tokens.
- Event-driven changes (AT-SPI events, X11 PropertyNotify) instead of re-reading the whole desktop each view.
- An ID render: paint each element's box in its own colour to label pixels for vision, from this model.
- Per-toolkit fixes where the mapping is wrong in a subtree (GTK4 header bar), learned from contradictions.
