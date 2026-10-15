"""Read the live desktop (X11, AT-SPI, OCR) into a Screen, and act on it with xdotool."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from any_app import describe_pixels, screenshot

from . import model, x11

ATSPI_TIMEOUT = 20


def read_atspi(max_elements: int = 1500) -> dict:
    """All AT-SPI windows, from a child process (a stuck accessibility bus cannot hang the caller)."""
    try:
        done = subprocess.run([sys.executable, "-m", "odd1_screen.atspi_dump", "--max-elements", str(max_elements)],
                              capture_output=True, timeout=ATSPI_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"apps": [], "error": "AT-SPI timed out"}
    if done.returncode:
        return {"apps": [], "error": done.stderr.decode("utf-8", "replace").strip()[-500:]}
    return json.loads(done.stdout)


def read_ocr(xid: str) -> list[dict] | None:
    """OCR one window's client area (image-local word boxes); None when it cannot be captured. any-app's capture
    renders the window offscreen (XComposite), so covered parts are read too."""
    with tempfile.TemporaryDirectory(prefix="odd1-screen-") as tmp:
        target = Path(tmp) / "window.png"
        try:
            screenshot.capture(xid, out=target)
            return describe_pixels.describe_image(target, max_words=3000)["elements"]
        except Exception:  # Xlib errors (a window closing mid-capture), OCR failures: no pixels this view
            return None


def observe(screen: model.Screen, *, window: str = "", ocr: str = "auto", include_hidden: bool = False,
            max_elements: int = 400) -> dict:
    """One fused view. window: a title substring or wN id to restrict to; ocr: auto (windows without a tree or
    without an established mapping), always, never."""
    if ocr not in ("auto", "always", "never"):
        raise ValueError("ocr must be auto, always or never")
    desktop = x11.windows()
    atspi = read_atspi()
    frames = model.match_frames(desktop["windows"], atspi.get("apps", []))
    words = {}
    for w in desktop["windows"]:
        if not w["geometry"]["viewable"] or not _selected(screen, w, window):
            continue
        if ocr == "always" or (ocr == "auto" and screen.needs_ocr(w, frames.get(w["xid"]))):
            found = read_ocr(w["xid"])
            if found is not None:
                words[w["xid"]] = found
    view = screen.fuse(desktop, atspi, words, include_hidden=include_hidden, max_elements=max_elements)
    if window:
        view = {**view, "windows": [w for w in view["windows"] if _selected(screen, w, window)]}
        if not view["windows"]:
            raise LookupError(f"no window matches {window!r}")
    if atspi.get("error"):
        view["atspi_error"] = atspi["error"]
    return view


def _selected(screen: model.Screen, w: dict, query: str) -> bool:
    if not query:
        return True
    return screen.window_ids.get(w["xid"]) == query or w.get("id") == query or \
        query.casefold() in w["title"].casefold()


def click(screen: model.Screen, element_id: str, *, button: int = 1, allow_unverified: bool = False,
          wait_ms: int = 300) -> dict:
    """Click an element's centre. Refused when OCR contradicted its box, or when neither it nor its window's mapping
    is verified, or when another window is on top at that pixel; then returns what changed."""
    window, element = screen.find(element_id)
    calibration = window.get("calibration") or {}
    if element.get("verified") is False and not allow_unverified:
        raise PermissionError(f"{element_id}: OCR found {element['name']!r} outside its reported box; the toolkit "
                              "misplaces it (act through the app, or pass allow_unverified)")
    trusted = element.get("source") == "ocr" or element.get("verified") or calibration.get("verified")
    if not trusted and not allow_unverified:
        raise PermissionError(f"{element_id}: its screen box is unverified ({calibration.get('evidence')}); "
                              "view with ocr='always' first, or pass allow_unverified")
    x, y = model.centre(element["box"])
    owner = screen.at(x, y)["window"]
    if owner is None or owner["id"] != window["id"]:
        raise PermissionError(f"{element_id}: covered by {owner and owner['title']!r} at ({x}, {y})")
    before = screen.last["view"]
    subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y), "click", str(button)], check=True,
                   capture_output=True, timeout=5)
    time.sleep(wait_ms / 1000)
    after = observe(screen, ocr="never")
    return {"clicked": {"id": element_id, "x": x, "y": y}, "changes": model.changes(before, after)}


def type_text(screen: model.Screen, text: str, *, element_id: str = "", wait_ms: int = 300, **click_args) -> dict:
    """Type into the focused window, after clicking element_id when given; returns what changed."""
    if element_id:
        click(screen, element_id, **click_args)
    before = screen.last["view"] if screen.last else observe(screen, ocr="never")
    subprocess.run(["xdotool", "type", "--delay", "20", "--", text], check=True, capture_output=True,
                   timeout=5 + len(text) / 20)
    time.sleep(wait_ms / 1000)
    after = observe(screen, ocr="never")
    return {"typed": len(text), "changes": model.changes(before, after)}
