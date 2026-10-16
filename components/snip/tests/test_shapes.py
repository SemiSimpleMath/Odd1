import datetime
from pathlib import Path

from odd1_snip import shapes

MODULE = (Path(__file__).parent.parent / "module.nix").read_text()


def test_a_drag_in_any_direction_is_the_same_rectangle():
    assert shapes.rect(10, 20, 110, 70) == (10, 20, 100, 50)
    assert shapes.rect(110, 70, 10, 20) == (10, 20, 100, 50)


def test_rectangles_are_cut_to_the_screen():
    assert shapes.clip((-10, -10, 50, 50), 100, 100) == (0, 0, 40, 40)
    assert shapes.clip((90, 90, 50, 50), 100, 100) == (90, 90, 10, 10)
    assert shapes.clip((200, 200, 5, 5), 100, 100) is None


def test_a_click_is_not_a_snip():
    assert not shapes.big_enough((5, 5, 1, 1))
    assert not shapes.big_enough(None)
    assert shapes.big_enough((5, 5, 3, 3))


def test_freeform_snips_the_box_around_the_path():
    assert shapes.bbox([(10, 10), (30, 5), (20, 40)]) == (10, 5, 21, 36)


def test_the_topmost_window_under_the_pointer_wins():
    desk, back, front = (0, 0, 1000, 800), (0, 0, 500, 500), (100, 100, 200, 200)
    stack = [desk, back, front]  # bottom to top
    assert shapes.window_at(stack, 150, 150) == front
    assert shapes.window_at(stack, 50, 50) == back
    assert shapes.window_at(stack, 900, 700) == desk
    assert shapes.window_at([back], 600, 600) is None


def test_snips_go_to_pictures_screenshots(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_PICTURES_DIR", raising=False)
    assert shapes.folder(tmp_path) == tmp_path / "Pictures" / "Screenshots"
    monkeypatch.setenv("XDG_PICTURES_DIR", str(tmp_path / "Pics"))
    assert shapes.folder() == tmp_path / "Pics" / "Screenshots"
    assert shapes.filename(datetime.datetime(2026, 10, 16, 9, 5, 3)) == "Snip 2026-10-16 090503.png"


def test_a_snip_never_replaces_another(tmp_path):
    (tmp_path / "Snip.png").write_bytes(b"")
    (tmp_path / "Snip (2).png").write_bytes(b"")
    assert shapes.free_path(tmp_path, "Snip.png") == tmp_path / "Snip (3).png"
    assert shapes.free_path(tmp_path, "Other.png") == tmp_path / "Other.png"


def test_the_windows_keys_start_it():
    for key in ('"Print" = "odd1-snip"', '"<Shift><Super>s" = "odd1-snip"', '"<Alt>Print" = "odd1-snip --mode window"'):
        assert key in MODULE
