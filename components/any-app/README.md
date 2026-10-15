# any-app
See any window on the Odd1 Desktop as compact JSON: its accessibility tree (AT-SPI) when the app has one, OCR words from its pixels when it does not.
Milestone: ★ (any app, through interfaces agents write themselves). The observation layer under per-app adapters.

## Use
- Agents: the MCP server `any-app` (registered for every agent):
  - `app_describe(window, max_elements)`: one window's AT-SPI tree, `{window, elements: [{id, role, name, bounds,
    window_bounds, states, visibility}], truncated}`; `window` is a title substring that must match exactly one window.
  - `app_ocr(png | window_id | nothing)`: local Tesseract words, `{image: {sha256, width, height}, elements:
    [{text, confidence, bounds}]}`; with no argument it captures the whole screen.
- Command line: `odd1-any-app describe "Calculator"`, `odd1-any-app ocr shot.png`, `odd1-any-app geometry "Calculator"`
  (AT-SPI vs X11 vs screenshot geometry), `odd1-any-app eval-ocr ocr.json labels.json` (token P/R/F1),
  `odd1-any-app eval-boxes ocr.json boxes.json [--iou-threshold 0.5]` (box localization).
- Python: `PYTHONPATH=$ODD1_ANY_APP_PYTHONPATH python3`, then `from any_app import describe_pixels`
  (`describe_image(path)`), `describe_accessible` (`build_description(desktop, pyatspi, title)`).
- All outputs are byte-bounded and fail closed (an ambiguous title or an incomplete desktop scan is an error, not a
  guess). Boxes are observations: `screen_alignment: unverified`, `action_safe: false`.

## Tests
`python -m pytest components/any-app/tests -q` runs offline anywhere (fake AT-SPI trees, synthetic PNGs, mocked
Tesseract). `test_live_desktop.py` runs an OCR matrix on disposable Tk windows and skips without DISPLAY, tesseract
and ImageMagick; the MCP tests skip without the `mcp` package (1.x, `FastMCP`). On Odd1: `odd1-any-app-test`.

## What was learned
- AT-SPI covers GTK (GNOME Calculator), Qt (FeatherPad) and LibreOffice's VCL; Firefox exposed no children and
  X11/Xt apps (xterm) expose nothing, so pixels are the fallback there.
- AT-SPI desktop and window extents disagreed with X11 window geometry on live GTK/Qt windows: do not click on
  AT-SPI boxes until `odd1-any-app geometry` shows they line up for that toolkit.
- OCR on two hand-labelled screens (Mousepad, xterm) scored token F1 0.95; that is two in-sample screens, not a
  benchmark.

## Next
- Calibrate coordinates per toolkit (AT-SPI and OCR boxes to screen pixels) so observations become click targets.
- A held-out labelled set of app screens to measure OCR recognition and box localization separately.
- Merge both sources into one screen model with stable IDs (the ★ screen-model service) instead of two tools.
