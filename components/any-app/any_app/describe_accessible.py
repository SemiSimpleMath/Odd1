#!/usr/bin/env python3
"""Emit a compact, geometry-aware description of an AT-SPI application window.

Requires the desktop's AT-SPI Python bindings (pyatspi). This reads the live
accessibility tree only; it does not capture pixels or perform actions.
"""
import argparse
import json
import operator
import sys

WINDOW_ROLES = {"frame", "window", "dialog"}
STATE_NAMES = ("SHOWING", "VISIBLE", "ENABLED", "SENSITIVE", "FOCUSED",
               "FOCUSABLE", "SELECTABLE", "SELECTED", "CHECKED", "EXPANDED")
# Bound per-accessible text normalization work as well as serialized output.
# AT-SPI labels are app-controlled and can be far larger than the emitted field.
MAX_ACCESSIBLE_TEXT_INPUT = 4096


def safe_attr(node, name, fallback=None):
    try:
        value = getattr(node, name)
        return value() if callable(value) else value
    except Exception:
        return fallback


def child_count(node):
    """Return (count, uncertain); inaccessible metadata must not imply a complete tree."""
    try:
        count = getattr(node, "childCount")
        count = count() if callable(count) else count
        # AT-SPI childCount is an integer contract. Avoid lossy coercion: a
        # fractional/string/bool value is malformed metadata, not an empty tree.
        if isinstance(count, bool):
            return 0, True
        count = operator.index(count)
        if count < 0:
            # An invalid count is not evidence of an empty branch; fail closed.
            return 0, True
        return count, False
    except Exception:
        return 0, True


def has_children_or_unknown(node):
    count, uncertain = child_count(node)
    return uncertain or count > 0


def children(node, limit=10000):
    """Read at most limit children, returning (indexed children, incomplete flag)."""
    count, incomplete = child_count(node)
    if count == 0:
        return (), incomplete
    try:
        getter = getattr(node, "getChildAtIndex", None)
    except Exception:
        return (), True
    if not callable(getter):
        return (), True
    result = []
    for index in range(min(count, max(0, limit))):
        try:
            item = getter(index)
        except Exception:
            incomplete = True
            continue
        if item is not None:
            result.append((index, item))
        else:
            incomplete = True
    return result, incomplete or count > limit


def display_text(value, fallback=""):
    """Return bounded-friendly display text that can always be UTF-8 encoded.

    Accessibility strings are app-controlled input. Some bindings can expose
    lone surrogate code points; those are not valid UTF-8 and would otherwise
    make the JSON serializer fail despite its advertised byte-bounded contract.
    Replace invalid scalar values before matching or emitting them.
    """
    try:
        text = str(value or fallback)
    except Exception:
        text = fallback
    # Limit work on provider-controlled strings before replacing invalid
    # scalars. Mark input truncation so consumers do not mistake a prefix for
    # the full label. Existing role/name field limits are applied afterward.
    if len(text) > MAX_ACCESSIBLE_TEXT_INPUT:
        text = text[:MAX_ACCESSIBLE_TEXT_INPUT - 1] + "…"
    return "".join("\ufffd" if 0xD800 <= ord(char) <= 0xDFFF else char
                   for char in text)


def role_name(node):
    value = safe_attr(node, "getRoleName", "")
    return " ".join(display_text(value, "unknown").split())[:80]


def node_name(node):
    value = safe_attr(node, "name", "")
    return " ".join(display_text(value).split())[:240]


def find_windows(desktop, max_nodes=10000, max_depth=48):
    """Return (matches, visited count, truncated) with bounded traversal storage."""
    found, visited, truncated = [], 0, False
    stack = [("", desktop, 0)]
    scheduled = 1
    while stack and visited < max_nodes:
        path, node, depth = stack.pop()
        visited += 1
        role = role_name(node)
        name = node_name(node)
        if role.casefold() in WINDOW_ROLES:
            found.append((path, node, role, name))
        if depth < max_depth:
            allowance = max_nodes - scheduled
            if allowance <= 0:
                if has_children_or_unknown(node):
                    truncated = True
                continue
            descendants, omitted = children(node, limit=allowance)
            truncated = truncated or omitted
            for index, child in reversed(descendants):
                stack.append((f"{path}/{index}" if path else str(index), child, depth + 1))
            scheduled += len(descendants)
        elif has_children_or_unknown(node):
            truncated = True
    return found, visited, truncated or bool(stack)


def bounds(node, pyatspi, coordinate_space="desktop"):
    """Read integer extents in one explicitly selected AT-SPI coordinate space.

    ``desktop`` and ``window`` extents are observations, not guaranteed screen
    coordinates. Callers must not combine or act on them without calibration.
    """
    constant_name = {"desktop": "DESKTOP_COORDS", "window": "WINDOW_COORDS"}.get(coordinate_space)
    if constant_name is None:
        raise ValueError("coordinate_space must be 'desktop' or 'window'")
    coordinate_constant = getattr(pyatspi, constant_name, None)
    if coordinate_constant is None:
        return None
    try:
        rect = node.queryComponent().getExtents(coordinate_constant)
        values = []
        for field in ("x", "y", "width", "height"):
            value = getattr(rect, field)
            # AT-SPI extents are integer coordinates. Avoid silently truncating
            # floats or accepting bools/strings as plausible screen positions.
            if isinstance(value, bool):
                return None
            value = operator.index(value)
            values.append(value)
        if values[2] < 0 or values[3] < 0:
            return None
        return dict(zip(("x", "y", "width", "height"), values))
    except Exception:
        return None


# Roles whose text content is their value (a display, an entry, a terminal); never password fields.
TEXT_ROLES = {"text", "entry", "terminal", "document text", "editbar", "spin button", "paragraph"}
MAX_TEXT_VALUE = 2000


def text_value(node, role):
    """The text an editable or display element holds (AT-SPI Text interface), bounded; None if it has none."""
    if role not in TEXT_ROLES:
        return None
    try:
        text = node.queryText()
        count = operator.index(text.characterCount)
        count = max(count, 0)
        # A terminal's latest output is at the end of its scrollback.
        start = max(0, count - MAX_TEXT_VALUE) if role == "terminal" else 0
        value = text.getText(start, min(count, start + MAX_TEXT_VALUE))
    except Exception:
        return None
    value = display_text(value)
    return value[:MAX_TEXT_VALUE] if value else None


def state_snapshot(node, pyatspi):
    """Return known state flags and tri-state AT-SPI SHOWING status.

    Absence of SHOWING is meaningful only when both the state set and
    toolkit constant were readable; otherwise visibility remains unknown.
    """
    state_set = safe_attr(node, "getState", None)
    if state_set is None:
        return [], "unknown"

    showing_constant = getattr(pyatspi, "STATE_SHOWING", None)
    showing = None
    if showing_constant is not None:
        try:
            showing = bool(state_set.contains(showing_constant))
        except Exception:
            pass
    visibility = ("showing" if showing else "not_showing") if showing is not None else "unknown"

    present = []
    for name in STATE_NAMES:
        constant = getattr(pyatspi, "STATE_" + name, None)
        if constant is not None:
            try:
                is_present = showing if name == "SHOWING" and showing is not None else state_set.contains(constant)
                if is_present:
                    present.append(name.lower())
            except Exception:
                pass
    return present, visibility


def describe_window(window, pyatspi, max_elements=1000, max_depth=32):
    elements, visited, truncated = [], 0, False
    stack = [("", window, 0)]
    scheduled = 1
    while stack and len(elements) < max_elements:
        path, node, depth = stack.pop()
        visited += 1
        role = role_name(node)
        name = node_name(node)
        states, visibility = state_snapshot(node, pyatspi)
        elements.append({"id": path or "root", "role": role,
                         "name": name,
                         # Retain both provider coordinate interpretations for
                         # independent comparison; neither is action-safe absent
                         # calibration. ``bounds`` remains desktop-space for API
                         # compatibility, while window_bounds is additive.
                         "bounds": bounds(node, pyatspi, "desktop"),
                         "window_bounds": bounds(node, pyatspi, "window"),
                         "states": states, "visibility": visibility})
        value = text_value(node, role)
        if value is not None:
            elements[-1]["text"] = value
        if depth < max_depth:
            allowance = max_elements - scheduled
            if allowance <= 0:
                if has_children_or_unknown(node):
                    truncated = True
                continue
            descendants, omitted = children(node, limit=allowance)
            truncated = truncated or omitted
            for index, child in reversed(descendants):
                stack.append((f"{path}/{index}" if path else str(index), child, depth + 1))
            scheduled += len(descendants)
        elif has_children_or_unknown(node):
            truncated = True
    return elements, truncated or bool(stack), visited


def build_description(desktop, pyatspi, window_query, max_elements=1000):
    query = str(window_query).strip().casefold()
    if not query:
        raise ValueError('window title query must not be empty')
    matches, scanned, scan_truncated = find_windows(desktop)
    # A truncated desktop scan cannot establish that the title match is unique.
    # Do not select a candidate when inaccessible/omitted branches may hide a duplicate.
    if scan_truncated:
        raise LookupError("desktop accessibility scan incomplete; refusing uncertain window selection")
    candidates = [item for item in matches if query in item[3].casefold()]
    if not candidates:
        titles = [name for _, _, _, name in matches if name]
        raise LookupError("no matching accessible window; found: " + ", ".join(titles[:20]))
    # Ambiguous title matches are unsafe to guess between. Caller can narrow query.
    if len(candidates) != 1:
        raise LookupError("window query is ambiguous: " + ", ".join(x[3] for x in candidates[:20]))
    path, window, role, title = candidates[0]
    elements, truncated, visited = describe_window(window, pyatspi, max_elements=max_elements)
    return {"source": "at-spi", "window": {"title": title, "role": role,
            "desktop_path": path},
            # Provider coordinates must not be treated as screen/action coordinates
            # until the app/toolkit's coordinate contract is independently validated.
            "geometry": {"coordinate_space": "atspi-desktop-coords",
                         "secondary_coordinate_space": "atspi-window-coords",
                         "screen_alignment": "unverified"},
            "elements": elements, "truncated": truncated,
            "scan_truncated": scan_truncated,
            "counts": {"window_elements": visited, "windows_scanned": scanned}}


def serialize_description(description, max_bytes=262144):
    """Return compact UTF-8 JSON no larger than max_bytes, or fail closed.

    Element details are removed from the end of the snapshot until the complete
    JSON document fits. Metadata always records output truncation and the number
    of emitted elements; source traversal counts remain unchanged.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    elements = list(description.get("elements", []))
    original_count = len(elements)

    def candidate(count):
        result = dict(description)
        result["elements"] = elements[:count]
        result["counts"] = dict(description.get("counts", {}))
        result["counts"]["emitted_elements"] = count
        omitted = count < original_count
        result["output_truncated"] = omitted
        result["truncated"] = bool(description.get("truncated", False) or omitted)
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        return encoded

    # Find the longest prefix that fits in O(log N) serializations rather than
    # repeatedly dumping a large tree while dropping one element at a time.
    low, high = 0, original_count
    best = None
    while low <= high:
        middle = (low + high) // 2
        encoded = candidate(middle)
        if len(encoded.encode("utf-8")) <= max_bytes:
            best = encoded
            low = middle + 1
        else:
            high = middle - 1
    if best is None:
        raise ValueError("max_bytes too small for description metadata")
    return best


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("window", help="unique, case-insensitive substring of accessible window title")
    parser.add_argument("--max-elements", type=int, default=1000)
    parser.add_argument("--max-bytes", type=int, default=262144,
                        help="maximum UTF-8 JSON output bytes (default: 262144)")
    args = parser.parse_args(argv)
    if not 1 <= args.max_elements <= 10000:
        parser.error("--max-elements must be between 1 and 10000")
    if not 256 <= args.max_bytes <= 1048576:
        parser.error("--max-bytes must be between 256 and 1048576")
    try:
        import pyatspi
        desktop = pyatspi.Registry.getDesktop(0)
        description = build_description(desktop, pyatspi, args.window, args.max_elements)
        # Include the trailing CLI newline in the advertised stdout byte cap.
        output = serialize_description(description, args.max_bytes - 1)
    except ImportError as exc:
        print(f"AT-SPI bindings unavailable: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"cannot describe window: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
