"""The screen model: X11 windows, AT-SPI trees and OCR words fused into one view with screen coordinates, stable
ids and change events. Pure functions over the readers' JSON, so every rule here tests without a display.

Coordinates. X11 is the authority on where a window is. An AT-SPI tree's boxes are mapped to the screen by one of
three per-window mappings, chosen by evidence: OCR must find an element's name inside its mapped box.
  desktop         the toolkit's desktop coordinates as they are (Qt, Firefox)
  window          X11 client origin + the toolkit's window coordinates (GTK4 reports desktop 0,0)
  frame-relative  X11 client origin + (element - frame) in desktop coordinates
A window is `verified` when the chosen mapping put at least two named elements (or all checkable ones) over their
own text. An element is `verified: true` when OCR found its name inside its box, `false` (contradicted) when OCR
found its name only elsewhere; a toolkit can get one subtree wrong (GTK4's header bar). Verdicts are kept while an
element's box stays put. Unverified boxes are observations, not click targets.
"""
import re

MAPPINGS = ("desktop", "window", "frame-relative")
# Elements worth showing an agent; nameless containers are dropped.
INTERACTIVE = {"push button", "toggle button", "check box", "radio button", "entry", "password text", "text",
               "combo box", "menu item", "check menu item", "radio menu item", "menu", "link", "slider",
               "spin button", "page tab", "list item", "table cell", "tree item", "terminal", "document web",
               "document text", "document frame", "editbar", "scroll bar", "icon"}
STATE_KEYS = ("focused", "checked", "selected", "expanded", "enabled")
MIN_OCR_CONFIDENCE = 40


def norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def to_box(bounds: dict | None) -> list[int] | None:
    if not bounds or bounds.get("width", 0) <= 0 or bounds.get("height", 0) <= 0:
        return None
    return [bounds["x"], bounds["y"], bounds["width"], bounds["height"]]


def contains(box, x, y, pad=0) -> bool:
    return box[0] - pad <= x < box[0] + box[2] + pad and box[1] - pad <= y < box[1] + box[3] + pad


def centre(box) -> tuple[int, int]:
    return box[0] + box[2] // 2, box[1] + box[3] // 2


def map_box(element: dict, frame: dict, origin: tuple[int, int], mapping: str) -> list[int] | None:
    """An AT-SPI element's box in screen pixels under one mapping (None when the toolkit gave no extents)."""
    if mapping == "desktop":
        return to_box(element.get("bounds"))
    if mapping == "window":
        box = to_box(element.get("window_bounds"))
        return box and [origin[0] + box[0], origin[1] + box[1], box[2], box[3]]
    box, base = to_box(element.get("bounds")), to_box(frame.get("bounds"))
    return box and base and [origin[0] + box[0] - base[0], origin[1] + box[1] - base[1], box[2], box[3]]


def ocr_hits(name: str, words: list[dict]) -> list[list[int]]:
    """Boxes of OCR words (image-local) that spell this element's name or a word of it."""
    target = norm(name)[:40]
    if not target:
        return []
    hits = []
    for word in words:
        w = norm(word["text"])
        if w and (w == target or (len(w) >= 3 and w in target)):
            hits.append(to_box(word["bounds"]))
    return [h for h in hits if h]


def calibrate(frame: dict, elements: list[dict], geometry: dict, words: list[dict] | None) -> dict:
    """Choose the window's mapping. With OCR words: the mapping that puts the most named elements over their own
    text. Without: desktop if the toolkit's frame box equals the X11 client box, else window (unverified)."""
    origin = (geometry["x"], geometry["y"])
    if words:
        checkable = [(e, hits) for e in elements if e.get("name") and e.get("visibility") != "not_showing"
                     for hits in [ocr_hits(e["name"], words)] if hits]
        scores = {}
        for mapping in MAPPINGS:
            score = 0
            for element, hits in checkable:
                box = map_box(element, frame, origin, mapping)
                if box and any(contains(box, *centre([h[0] + origin[0], h[1] + origin[1], h[2], h[3]]), pad=4)
                               for h in hits):
                    score += 1
            scores[mapping] = score
        best = max(MAPPINGS, key=lambda m: scores[m])  # ties keep MAPPINGS order
        score, total = scores[best], len(checkable)
        if score >= 2 or (score and score == total):
            return {"mapping": best, "verified": True, "evidence": f"{score}/{total} named elements over their text"}
        if total:
            return {"mapping": best if score else _default(frame, geometry), "verified": False,
                    "evidence": f"{score}/{total} named elements over their text"}
    return {"mapping": _default(frame, geometry), "verified": False, "evidence": "no OCR evidence"}


def _default(frame: dict, geometry: dict) -> str:
    box = to_box(frame.get("bounds"))
    x11 = [geometry["x"], geometry["y"], geometry["width"], geometry["height"]]
    return "desktop" if box and all(abs(a - b) <= 2 for a, b in zip(box, x11)) else "window"


def ocr_lines(words: list[dict]) -> list[dict]:
    """Group confident OCR words into lines: {text, box (image-local), confidence}."""
    good = sorted((w for w in words if w["confidence"] >= MIN_OCR_CONFIDENCE and to_box(w["bounds"])),
                  key=lambda w: (w["bounds"]["y"] + w["bounds"]["height"] / 2, w["bounds"]["x"]))
    lines: list[dict] = []
    for word in good:
        box = to_box(word["bounds"])
        for line in lines:
            lb = line["box"]
            same_row = abs((lb[1] + lb[3] / 2) - (box[1] + box[3] / 2)) <= max(lb[3], box[3]) / 2
            if same_row and 0 <= box[0] - (lb[0] + lb[2]) <= 2 * max(lb[3], box[3]):
                x2, y2 = box[0] + box[2], max(lb[1] + lb[3], box[1] + box[3])
                line["box"] = [lb[0], min(lb[1], box[1]), x2 - lb[0], y2 - min(lb[1], box[1])]
                line["text"] += " " + word["text"]
                line["confidence"] = min(line["confidence"], word["confidence"])
                break
        else:
            lines.append({"text": word["text"], "box": box, "confidence": word["confidence"]})
    return lines


def topmost(windows: list[dict], x: int, y: int) -> dict | None:
    """The window that owns screen pixel (x, y): the highest viewable one containing it (windows bottom first)."""
    for window in reversed(windows):
        g = window["geometry"]
        if g["viewable"] and "hidden" not in window["states"] and contains([g["x"], g["y"], g["width"], g["height"]], x, y):
            return window
    return None


def match_frames(windows: list[dict], apps: list[dict]) -> dict[str, dict]:
    """X11 window id -> its AT-SPI frame: same process and title; size breaks ties."""
    matched = {}
    frames = [(app, frame) for app in apps for frame in app["frames"]]
    for window in windows:
        candidates = [f for app, f in frames if app["pid"] == window["pid"] and f["title"] == window["title"]]
        if len(candidates) > 1:
            g = window["geometry"]
            candidates = [f for f in candidates
                          if (to_box(f["elements"][0].get("bounds") if f["elements"] else None) or [0, 0, 0, 0])[2:]
                          == [g["width"], g["height"]]] or candidates[:1]
        if candidates:
            matched[window["xid"]] = candidates[0]
    return matched


class Screen:
    """Fuses snapshots and keeps ids stable between them: a window keeps `wN` while its X11 window lives; an element
    keeps `wN.eM` while its role, name and ancestors' roles stay the same (the k-th of identical ones keeps k)."""

    def __init__(self):
        self.window_ids: dict[str, str] = {}
        self.element_ids: dict[tuple, str] = {}
        self.calibrations: dict[tuple, dict] = {}
        self.verdicts: dict[str, tuple] = {}  # element id -> (box, verified)
        self.last: dict | None = None
        self._next = {"w": 0}

    def _window_id(self, xid: str) -> str:
        if xid not in self.window_ids:
            self._next["w"] += 1
            self.window_ids[xid] = f"w{self._next['w']}"
            self._next[self.window_ids[xid]] = 0
        return self.window_ids[xid]

    def _element_id(self, wid: str, signature: tuple) -> str:
        key = (wid, *signature)
        if key not in self.element_ids:
            self._next[wid] += 1
            self.element_ids[key] = f"{wid}.e{self._next[wid]}"
        return self.element_ids[key]

    def calibration_key(self, window: dict, frame: dict | None) -> tuple:
        g = window["geometry"]
        return (window["xid"], g["width"], g["height"], len(frame["elements"]) if frame else 0)

    def needs_ocr(self, window: dict, frame: dict | None) -> bool:
        """OCR a window when it has no tree (pixels are all there is) or its mapping is not yet established."""
        return frame is None or not frame["elements"] or self.calibration_key(window, frame) not in self.calibrations

    def fuse(self, x11: dict, atspi: dict, ocr: dict[str, list[dict]], *, include_hidden: bool = False,
             max_elements: int = 400) -> dict:
        windows = x11["windows"]
        frames = match_frames(windows, atspi.get("apps", []))
        live = {w["xid"] for w in windows}
        self.window_ids = {k: v for k, v in self.window_ids.items() if k in live}
        result = []
        for window in windows:
            wid = self._window_id(window["xid"])
            g = window["geometry"]
            frame = frames.get(window["xid"])
            words = ocr.get(window["xid"])
            elements: list[dict] = []
            tree = frame["elements"] if frame else []
            calibration = None
            if tree:
                key = self.calibration_key(window, frame)
                if words is not None or key not in self.calibrations:
                    fresh = calibrate(tree[0], tree, g, words)
                    # Keep verified evidence; an unverified result never replaces it.
                    if fresh["verified"] or not self.calibrations.get(key, {}).get("verified"):
                        self.calibrations[key] = fresh
                calibration = self.calibrations[key]
                elements = self._tree_elements(wid, window, tree, calibration["mapping"], words, include_hidden)
            if words:
                elements += self._text_elements(wid, window, windows, words, elements)
            truncated = len(elements) > max_elements or bool(frame and frame["truncated"])
            result.append({"id": wid, "xid": window["xid"], "title": window["title"], "pid": window["pid"],
                           "box": [g["x"], g["y"], g["width"], g["height"]],
                           "active": window["xid"] == x11.get("active"),
                           "states": window["states"],
                           "source": "at-spi" if tree else ("ocr" if words else "none"), "ocr": words is not None,
                           "calibration": calibration, "elements": elements[:max_elements], "truncated": truncated})
        view = {"screen": x11["screen"], "windows": list(reversed(result))}  # topmost first
        self.last = {"view": view, "x11": windows}
        return view

    def _tree_elements(self, wid, window, tree, mapping, words, include_hidden):
        frame, g = tree[0], window["geometry"]
        origin = (g["x"], g["y"])
        roles = {e["id"]: e["role"] for e in tree}
        seen: dict[tuple, int] = {}
        out = []
        for element in tree[1:]:
            name, role = element.get("name", ""), element["role"]
            if not include_hidden and element.get("visibility") == "not_showing":
                continue
            if not name and role not in INTERACTIVE:
                continue
            box = map_box(element, frame, origin, mapping)
            if box is None:
                continue
            parts = element["id"].split("/")
            ancestors = tuple(roles.get("/".join(parts[:i]), "?") for i in range(1, len(parts)))
            signature = (role, name, ancestors)
            seen[signature] = seen.get(signature, 0) + 1
            item = {"id": self._element_id(wid, (*signature, seen[signature])), "role": role, "name": name,
                    "box": box, "states": [s for s in element.get("states", []) if s in STATE_KEYS]}
            if "text" in element:
                item["value"] = element["text"]
            hits = ocr_hits(name, words) if words is not None and name else []
            inside = any(contains(box, *centre([h[0] + g["x"], h[1] + g["y"], h[2], h[3]]), pad=4) for h in hits)
            # Short texts repeat on screen ("15" in a display and in a history), so only a name of 3+ characters
            # seen only elsewhere contradicts a box.
            if inside or (hits and len(norm(name)) >= 3):
                self.verdicts[item["id"]] = (box, inside)
            if self.verdicts.get(item["id"], (None,))[0] == box:
                item["verified"] = self.verdicts[item["id"]][1]
            out.append(item)
        return out

    def _text_elements(self, wid, window, windows, words, elements):
        """OCR lines that no tree element already names; `covered` when another window is on top of them (the
        capture is offscreen, so their text is real, but a click there would land on the other window)."""
        g = window["geometry"]
        named = [(norm(e["name"]), e["box"]) for e in elements if e["name"]]
        seen: dict[str, int] = {}
        out = []
        for line in ocr_lines(words):
            box = [line["box"][0] + g["x"], line["box"][1] + g["y"], line["box"][2], line["box"][3]]
            owner = topmost(windows, *centre(box))
            text = norm(line["text"])
            if any(text and text in n and contains(b, *centre(box), pad=4) for n, b in named):
                continue
            seen[line["text"]] = seen.get(line["text"], 0) + 1
            out.append({"id": self._element_id(wid, ("text", line["text"], (), seen[line["text"]])),
                        "role": "text", "name": line["text"], "box": box, "states": [], "source": "ocr",
                        "confidence": line["confidence"]})
            if owner is None or owner["xid"] != window["xid"]:
                out[-1]["covered"] = True
        return out

    def at(self, x: int, y: int) -> dict:
        """Who owns screen pixel (x, y) in the last view: the topmost window and its smallest element there."""
        if not self.last:
            raise LookupError("no view yet")
        owner = topmost(self.last["x11"], x, y)
        if owner is None:
            return {"x": x, "y": y, "window": None, "element": None}
        window = next(w for w in self.last["view"]["windows"] if w["xid"] == owner["xid"])
        inside = [e for e in window["elements"] if contains(e["box"], x, y)]
        element = min(inside, key=lambda e: e["box"][2] * e["box"][3], default=None)
        return {"x": x, "y": y, "window": {k: window[k] for k in ("id", "title", "box", "calibration")},
                "element": element}

    def find(self, element_id: str) -> tuple[dict, dict]:
        if not self.last:
            raise LookupError("no view yet")
        for window in self.last["view"]["windows"]:
            for element in window["elements"]:
                if element["id"] == element_id:
                    return window, element
        raise LookupError(f"no element {element_id} in the last view")


def changes(before: dict | None, after: dict) -> dict:
    """What changed between two views: windows opened/closed/moved, elements added/removed/changed."""
    old = {w["id"]: w for w in (before or {"windows": []})["windows"]}
    new = {w["id"]: w for w in after["windows"]}
    result = {"windows_opened": [{k: new[i][k] for k in ("id", "title", "box")} for i in new if i not in old],
              "windows_closed": [i for i in old if i not in new], "windows_changed": [],
              "elements_added": [], "elements_removed": [], "elements_changed": []}
    for wid in new.keys() & old.keys():
        diff = {k: new[wid][k] for k in ("title", "box", "active") if new[wid][k] != old[wid][k]}
        if diff:
            result["windows_changed"].append({"id": wid, **diff})
        # OCR text is compared only between two views that both read the window's pixels.
        both_ocr = old[wid].get("ocr") and new[wid].get("ocr")
        before_e = {e["id"]: e for e in old[wid]["elements"] if both_ocr or e.get("source") != "ocr"}
        after_e = {e["id"]: e for e in new[wid]["elements"] if both_ocr or e.get("source") != "ocr"}
        result["elements_added"] += [after_e[i] for i in after_e if i not in before_e]
        result["elements_removed"] += [i for i in before_e if i not in after_e]
        for i in after_e.keys() & before_e.keys():
            diff = {k: after_e[i].get(k) for k in ("name", "value", "box", "states") if after_e[i].get(k) != before_e[i].get(k)}
            if diff:
                result["elements_changed"].append({"id": i, **diff})
    return result
