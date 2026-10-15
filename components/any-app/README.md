# any-app
See any window on the Odd1 Desktop as compact JSON: its accessibility tree (AT-SPI) when the app has one, OCR words from its pixels when it does not.
Milestone: ★ (any app, through interfaces agents write themselves). The observation layer under per-app adapters.

## Use
- Agents: the MCP server `any-app` (registered for every agent):
  - `app_describe(window, max_elements)`: one window's AT-SPI tree, `{window, elements: [{id, role, name, bounds,
    window_bounds, states, visibility, text?}], truncated}`; `window` is a title substring that must match exactly
    one window. `text` is a display's, entry's or terminal's content (a terminal's latest 2,000 characters; never
    a password field's).
  - `app_ocr(png | window_id | nothing)`: local Tesseract words, `{image: {sha256, width, height}, elements:
    [{text, confidence, bounds}]}`; with no argument it captures the whole screen.
  - `app_screenshot(window, out, frame)`: one window as a PNG even when other windows cover it or it hangs off
    screen, `{path, window, title, app, width, height, sha256}`; `window` is a title or app-name (WM_CLASS)
    substring matching exactly one window, or an X11 id. Read the PNG at `path` to see it.
- Command line: `odd1-any-app describe "Calculator"`, `odd1-any-app ocr shot.png`, `odd1-any-app screenshot featherpad [-o out.png] [--frame]`, `odd1-any-app geometry "Calculator"`
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

- Covered windows: the Desktop has no compositor, so covered pixels do not exist and `import -window` returns
  black for them. `screenshot` redirects the window's frame with XComposite (automatic mode, the screen is
  unchanged), waits 0.3 s for the app to repaint into its offscreen pixmap, and reads that. GTK, Qt, Firefox, Tk
  all repaint; a minimized (unmapped) window has no pixels and is an error. `app_ocr(window_id)` uses it too.

- OCR reads each image twice in parallel, as given and in grayscale at 2x, and keeps whichever pass read each region
  more confidently: small fonts gain (FeatherPad 10 -> 19 words at confidence >= 60, xterm 0 -> 3) and large text
  keeps its 1x reading (2x alone hurt Calculator). Colour tricks (inversion, background distance) did not help;
  xterm's bitmap font stays poor (`brouser`). Tesseract runs single-threaded (OMP_THREAD_LIMIT=1): parallel
  multithreaded passes oversubscribed the CPUs and timed out. 0.3-0.6 s per window.

## Next
- Calibrate coordinates per toolkit (AT-SPI and OCR boxes to screen pixels) so observations become click targets.
- A held-out labelled set of app screens to measure OCR recognition and box localization separately.
- The merged screen model with stable ids now lives in `screen`, which uses this component's readers; grow the
  readers here and the fusion there.
