"""The fusion rules on synthetic desktops: mapping choice by OCR evidence, stable ids, occlusion, changes."""
import copy

import pytest

from odd1_screen import model


def window(xid, title, x, y, w, h, pid=1, states=()):
    return {"xid": xid, "title": title, "pid": pid, "states": list(states),
            "geometry": {"x": x, "y": y, "width": w, "height": h, "viewable": True}}


def element(path, role, name, window_box, desktop_box=(0, 0, 0, 0), **extra):
    """A GTK4-like element: right window coordinates, desktop coordinates stuck at the origin."""
    box = lambda b: dict(zip(("x", "y", "width", "height"), b))
    return {"id": path, "role": role, "name": name, "bounds": box(desktop_box or window_box),
            "window_bounds": box(window_box), "states": ["enabled"], "visibility": "showing", **extra}


def word(text, x, y, w=20, h=12, confidence=90):
    return {"text": text, "confidence": confidence, "bounds": {"x": x, "y": y, "width": w, "height": h}}


def calculator():
    tree = [element("root", "frame", "Calculator", (0, 0, 400, 600), (0, 0, 400, 600)),
            element("0", "push button", "Seven", (20, 400, 70, 40), (0, 0, 70, 40)),
            element("1", "push button", "Eight", (100, 400, 70, 40), (0, 0, 70, 40)),
            element("2", "text", "", (10, 300, 380, 40), (0, 0, 380, 40), text="15"),
            element("3", "filler", "", (0, 0, 400, 600), (0, 0, 400, 600))]
    x = {"screen": {"width": 1280, "height": 800}, "active": "0x2",
         "windows": [window("0x1", "xterm", 0, 0, 600, 300, pid=9), window("0x2", "Calculator", 500, 0, 400, 600)]}
    atspi = {"apps": [{"app": "calc", "pid": 1, "frames": [{"title": "Calculator", "role": "frame",
                                                             "elements": tree, "truncated": False}]}]}
    words = [word("Seven", 30, 410), word("Eight", 110, 410)]
    return x, atspi, words


def test_mapping_chosen_by_ocr_evidence_gives_screen_boxes():
    x, atspi, words = calculator()
    view = model.Screen().fuse(x, atspi, {"0x2": words})
    calc = view["windows"][0]  # topmost first
    assert calc["title"] == "Calculator" and calc["active"]
    assert calc["calibration"]["mapping"] == "window" and calc["calibration"]["verified"]
    seven = next(e for e in calc["elements"] if e["name"] == "Seven")
    assert seven["box"] == [520, 400, 70, 40] and seven["verified"] is True
    display = next(e for e in calc["elements"] if e["role"] == "text")
    assert display["value"] == "15"
    assert not any(e["role"] == "filler" for e in calc["elements"])  # nameless containers dropped


def test_without_evidence_the_mapping_is_unverified():
    x, atspi, _ = calculator()
    calc = model.Screen().fuse(x, atspi, {})["windows"][0]
    assert calc["calibration"] == {"mapping": "window", "verified": False, "evidence": "no OCR evidence"}
    assert "verified" not in calc["elements"][0]


def test_contradicted_element_and_short_names_never_contradict():
    x, atspi, words = calculator()
    tree = atspi["apps"][0]["frames"][0]["elements"]
    tree.append(element("4", "push button", "Undo", (0, -5, 80, 30), (0, 0, 80, 30)))  # misplaced header bar
    tree.append(element("5", "label", "15", (10, 200, 40, 20), (0, 0, 40, 20)))       # "15" also in the display
    words += [word("Undo", 40, 60), word("15", 300, 310)]
    calc = model.Screen().fuse(x, atspi, {"0x2": words})["windows"][0]
    by_name = {e["name"]: e for e in calc["elements"] if e.get("source") != "ocr"}
    assert by_name["Undo"]["verified"] is False
    assert "verified" not in by_name["15"]
    # The OCR line shows where the misplaced button really is.
    assert any(e["name"] == "Undo" and e["box"] == [540, 60, 20, 12] for e in calc["elements"] if e.get("source") == "ocr")


def test_verdicts_and_mapping_survive_views_without_ocr():
    x, atspi, words = calculator()
    screen = model.Screen()
    screen.fuse(x, atspi, {"0x2": words})
    assert not screen.needs_ocr(x["windows"][1], atspi["apps"][0]["frames"][0])
    calc = screen.fuse(x, atspi, {})["windows"][0]
    assert calc["calibration"]["verified"]
    assert next(e for e in calc["elements"] if e["name"] == "Seven")["verified"] is True


def test_ids_stay_stable_when_a_sibling_is_inserted():
    x, atspi, words = calculator()
    screen = model.Screen()
    first = {e["name"] or e["role"]: e["id"] for e in screen.fuse(x, atspi, {"0x2": words})["windows"][0]["elements"]}
    tree = atspi["apps"][0]["frames"][0]["elements"]
    tree.insert(1, element("0", "push button", "Nine", (180, 400, 70, 40), (0, 0, 70, 40)))
    for i, e in enumerate(tree[2:], start=1):
        e["id"] = str(i)
    second = {e["name"] or e["role"]: e["id"] for e in screen.fuse(x, atspi, {})["windows"][0]["elements"]}
    assert {k: second[k] for k in first} == first
    assert second["Nine"] not in first.values()


def test_ocr_text_is_kept_only_where_the_window_is_on_top():
    x, atspi, _ = calculator()
    # xterm (no tree) is under the calculator from x=500; its second line is covered.
    words = [word("ls", 10, 10), word("-l", 40, 10), word("hidden", 520, 50, w=60)]
    xterm = model.Screen().fuse(x, atspi, {"0x1": words})["windows"][1]
    assert xterm["source"] == "ocr"
    assert [e["name"] for e in xterm["elements"]] == ["ls -l"]


def test_at_names_the_owner_of_a_pixel():
    x, atspi, words = calculator()
    screen = model.Screen()
    screen.fuse(x, atspi, {"0x2": words})
    hit = screen.at(530, 410)
    assert hit["window"]["title"] == "Calculator" and hit["element"]["name"] == "Seven"
    assert screen.at(100, 100)["window"]["title"] == "xterm"
    assert screen.at(1200, 700)["window"] is None


def test_changes_report_values_windows_and_ignore_unread_ocr():
    x, atspi, words = calculator()
    screen = model.Screen()
    before = screen.fuse(x, atspi, {"0x1": [word("prompt", 10, 10, w=60)], "0x2": words})
    atspi2 = copy.deepcopy(atspi)
    atspi2["apps"][0]["frames"][0]["elements"][3]["text"] = "36"
    x2 = copy.deepcopy(x)
    x2["windows"].append(window("0x3", "Dialog", 100, 100, 200, 100, pid=5))
    after = screen.fuse(x2, atspi2, {})
    diff = model.changes(before, after)
    assert diff["windows_opened"][0]["title"] == "Dialog"
    assert diff["elements_changed"] == [{"id": diff["elements_changed"][0]["id"], "value": "36"}]
    assert diff["elements_removed"] == []  # the xterm's OCR line was not re-read, not removed


def test_frames_match_windows_by_process_and_title():
    x, atspi, _ = calculator()
    atspi["apps"].append({"app": "other", "pid": 2, "frames": [{"title": "Calculator", "role": "frame",
                                                                 "elements": [], "truncated": False}]})
    matched = model.match_frames(x["windows"], atspi["apps"])
    assert list(matched) == ["0x2"] and matched["0x2"]["elements"]


def test_ocr_lines_group_words_on_a_row():
    lines = model.ocr_lines([word("total", 0, 0, w=30), word("15", 36, 1), word("next", 0, 40), word("x", 300, 0),
                             word("noise", 0, 80, confidence=10)])
    assert [l["text"] for l in lines] == ["total 15", "x", "next"]


def test_find_and_at_need_a_view():
    with pytest.raises(LookupError):
        model.Screen().find("w1.e1")
