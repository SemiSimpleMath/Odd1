"""Read the live desktop (X11, AT-SPI, OCR) into a Screen, and act on it with xdotool."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from any_app import describe_pixels, screenshot

from . import consent, model, x11

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


def read_ocr(xid: str, display: str | None = None) -> list[dict] | None:
    """OCR one window's client area (image-local word boxes); None when it cannot be captured. any-app's capture
    renders the window offscreen (XComposite), so covered parts are read too."""
    with tempfile.TemporaryDirectory(prefix="odd1-screen-") as tmp:
        target = Path(tmp) / "window.png"
        try:
            screenshot.capture(xid, out=target, display_name=display)
            return describe_pixels.describe_image(target, max_words=3000)["elements"]
        except Exception:  # Xlib errors (a window closing mid-capture), OCR failures: no pixels this view
            return None


def observe(screen: model.Screen, *, window: str = "", ocr: str = "auto", include_hidden: bool = False,
            max_elements: int = 400) -> dict:
    """One fused view. window: a title substring or wN id to restrict to; ocr: auto (windows without a tree or
    without an established mapping), always, never."""
    if ocr not in ("auto", "always", "never"):
        raise ValueError("ocr must be auto, always or never")
    desktop = x11.windows(screen.display)
    atspi = read_atspi()
    frames = model.match_frames(desktop["windows"], atspi.get("apps", []))
    words = {}
    for w in desktop["windows"]:
        if not w["geometry"]["viewable"] or not _selected(screen, w, window):
            continue
        if ocr == "always" or (ocr == "auto" and screen.needs_ocr(w, frames.get(w["xid"]))):
            found = read_ocr(w["xid"], screen.display)
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


def _pointer_problem(screen: model.Screen, window: dict, element: dict, allow_unverified: bool) -> str | None:
    """Why the real pointer must not click this element, or None when it may."""
    if window.get("minimized"):
        return f"{element['id']}: its window is minimized (no place on screen)"
    calibration = window.get("calibration") or {}
    if element.get("verified") is False and not allow_unverified:
        return (f"{element['id']}: OCR found {element['name']!r} outside its reported box; the toolkit misplaces it")
    trusted = element.get("source") == "ocr" or element.get("verified") or calibration.get("verified")
    if not trusted and not allow_unverified:
        return f"{element['id']}: its screen box is unverified ({calibration.get('evidence')}); view with ocr='always'"
    x, y = model.centre(element["box"])
    owner = screen.at(x, y)["window"]
    if owner is None or owner["id"] != window["id"]:
        return f"{element['id']}: covered by {owner and owner['title']!r} at ({x}, {y})"
    return None


def _person_guard(screen: model.Screen, approved: bool, what: str, window: dict | None, reason: str = "") -> str:
    """The person's pointer, keyboard focus and windows are theirs: on their screen an agent acts only with their
    yes. person_approved=True means they said yes in the chat; otherwise a dialog on their screen asks them (a yes
    holds for that window a few minutes). Accessibility actions (via="app", set_text) and the agent desktop need
    no asking. Returns how it was allowed."""
    if screen.prefix != "w":
        return "agent desktop"
    if approved:
        return "approved in the chat"
    if window is None:
        raise PermissionError(f"{what} on the person's screen: no window to ask about")
    return consent.require(screen.display, window["xid"], window["title"], what, reason)


def _active_window(screen: model.Screen) -> str:
    done = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=5,
                          env=x11.env(screen.display))
    return done.stdout.strip()


def _app_act(address: dict, op: str, value: str | None = None) -> dict:
    """Act through the app's accessibility interface, in a child process (a stuck bus cannot hang the caller)."""
    args = [str(address["pid"]), str(address["frame"]), address["path"], address["role"], address["name"], op]
    done = subprocess.run([sys.executable, "-m", "odd1_screen.atspi_act", *args, *([value] if value is not None else [])],
                          capture_output=True, timeout=ATSPI_TIMEOUT)
    if done.returncode:
        raise RuntimeError(done.stderr.decode("utf-8", "replace").strip()[-500:] or "accessibility action failed")
    return json.loads(done.stdout)


def _settle(screen: model.Screen, before: dict, wait_ms: int) -> dict:
    time.sleep(wait_ms / 1000)
    return model.changes(before, observe(screen, ocr="never"))


def click(screen: model.Screen, element_id: str, *, via: str = "auto", button: int = 1,
          allow_unverified: bool = False, wait_ms: int = 300, person_approved: bool = False, reason: str = "") -> dict:
    """Press an element, then return what changed. via: "app" asks the app itself (accessibility action: works
    minimized or covered, and leaves the person's mouse alone), "pointer" clicks its centre with the real mouse
    (refused when minimized, covered, or its box is contradicted or unverified), "auto" uses the app when the
    element has an accessibility address and the pointer otherwise (OCR text)."""
    if via not in ("auto", "app", "pointer"):
        raise ValueError("via must be auto, app or pointer")
    window, element = screen.find(element_id)
    address = screen.addresses.get(element_id)
    before = screen.last["view"]
    if via == "app" or (via == "auto" and address and button == 1):
        if not address:
            raise PermissionError(f"{element_id}: OCR text has no accessibility address; use via='pointer'")
        done = _app_act(address, "action")
        return {"clicked": {"id": element_id, "via": "app", **done}, "changes": _settle(screen, before, wait_ms)}
    problem = _pointer_problem(screen, window, element, allow_unverified)
    if problem:
        raise PermissionError(problem)
    _person_guard(screen, person_approved, f"click {element['name'] or element_id!r} with your mouse in", window,
                  reason)
    x, y = model.centre(element["box"])
    subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y), "click", str(button)], check=True,
                   capture_output=True, timeout=5, env=x11.env(screen.display))
    return {"clicked": {"id": element_id, "via": "pointer", "x": x, "y": y},
            "changes": _settle(screen, before, wait_ms)}


def set_text(screen: model.Screen, element_id: str, text: str, *, wait_ms: int = 300) -> dict:
    """Replace an editable element's text through the app (works minimized or covered; never password fields)."""
    screen.find(element_id)
    address = screen.addresses.get(element_id)
    if not address:
        raise PermissionError(f"{element_id}: OCR text has no accessibility address; click it and use screen_type")
    before = screen.last["view"]
    _app_act(address, "set-text", text)
    return {"set_text": {"id": element_id, "length": len(text)}, "changes": _settle(screen, before, wait_ms)}


TYPE_CHUNK = 16  # characters typed between checks that the focus is still where typing began


def type_text(screen: model.Screen, text: str, *, element_id: str = "", wait_ms: int = 300,
              person_approved: bool = False, reason: str = "", **click_args) -> dict:
    """Type with the real keyboard into the focused window, after clicking element_id with the pointer when given
    (keystrokes go where the focus is; for a minimized or covered field use set_text). Typing goes in chunks and
    stops as soon as the focus moves to another window (a person or another agent took it)."""
    before = screen.last["view"] if screen.last else observe(screen, ocr="never")
    window = None
    if screen.prefix == "w" and not person_approved:  # the window the person is asked about
        window = screen.find(element_id)[0] if element_id else \
            next((w for w in observe(screen, ocr="never")["windows"] if w["active"]), None)
    allowed = _person_guard(screen, person_approved, "type with your keyboard into", window, reason)
    if element_id:  # one yes covers the click that puts the cursor in the field and the typing
        click(screen, element_id, via="pointer", person_approved=person_approved or allowed != "agent desktop",
              **click_args)
    target = _active_window(screen)
    for start in range(0, len(text), TYPE_CHUNK):
        if _active_window(screen) != target:
            raise RuntimeError(f"stopped after {start} of {len(text)} characters: the focus moved to another window")
        chunk = text[start:start + TYPE_CHUNK]
        subprocess.run(["xdotool", "type", "--delay", "20", "--", chunk], check=True, capture_output=True,
                       timeout=5 + len(chunk) / 20, env=x11.env(screen.display))
    return {"typed": len(text), "changes": _settle(screen, before, wait_ms)}


WINDOW_ACTIONS = {"maximize": "maximize", "restore": "restore (unmaximize)", "minimize": "minimize",
                  "move": "move or resize"}


def window_action(screen: model.Screen, window: str, action: str, *, box: list[int] | None = None,
                  person_approved: bool = False, reason: str = "", wait_ms: int = 300) -> dict:
    """Maximize, restore (unmaximize), minimize, or move/resize (box [x, y, width, height]; -1 keeps a value) one
    window, without activating it or taking the focus. On the person's screen the person is asked first (a dialog
    on their screen), unless person_approved. window: a window id (w3) or a title substring matching one window."""
    if action not in WINDOW_ACTIONS:
        raise ValueError(f"action must be one of {', '.join(WINDOW_ACTIONS)}")
    if action == "move" and (not box or len(box) != 4):
        raise ValueError("move needs box [x, y, width, height]")
    view = observe(screen, ocr="never", include_hidden=True)
    found = [w for w in view["windows"] if _selected(screen, w, window)]
    if len(found) != 1:
        listing = "; ".join(f"{w['id']} {w['title']!r}" for w in view["windows"])
        raise LookupError(f"{len(found)} windows match {window!r}; windows: {listing}")
    target = found[0]
    allowed = _person_guard(screen, person_approved, WINDOW_ACTIONS[action], target, reason)
    xid, env = target["xid"], x11.env(screen.display)
    commands = {
        "maximize": [["wmctrl", "-i", "-r", xid, "-b", "add,maximized_vert,maximized_horz"]],
        "restore": [["wmctrl", "-i", "-r", xid, "-b", "remove,maximized_vert,maximized_horz"]],
        "minimize": [["xdotool", "windowminimize", xid]],
        "move": [["wmctrl", "-i", "-r", xid, "-b", "remove,maximized_vert,maximized_horz"],
                 ["wmctrl", "-i", "-r", xid, "-e", "0," + ",".join(str(int(v)) for v in (box or []))]],
    }[action]
    for command in commands:
        subprocess.run(command, check=True, capture_output=True, timeout=5, env=env)
    return {"window": {"id": target["id"], "title": target["title"], "action": action, "allowed": allowed},
            "changes": _settle(screen, view, wait_ms)}
