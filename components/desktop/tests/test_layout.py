"""The desktop's geometry rules: the chat column, the work area, fitting windows, tabs, the wallpaper."""
import pytest

from odd1_desktop import layout


def test_chat_column_is_about_a_third_within_bounds():
    assert layout.dock_width(1280) == 461
    assert layout.dock_width(1920) == 680  # capped: never dominant
    assert layout.dock_width(1024) == 460  # floored: never cramped


def test_column_header_chat_and_work_area_tile_the_screen():
    screen, top = (1920, 1080), 34
    x, y, width, height = layout.column(screen, top)
    assert (x + width, y, y + height) == (1920, 34, 1080)
    assert layout.header_box(screen, top) == (x, y, width, layout.HEADER_HEIGHT)
    assert layout.chat_box(screen, top) == (x, y + layout.HEADER_HEIGHT, width, height - layout.HEADER_HEIGHT)
    assert layout.work_area(screen, top) == (0, 34, x, 1046)


def test_strut_reserves_the_column_on_the_right_below_the_bar():
    strut = layout.strut((1280, 800), 34)
    assert strut[1] == 461 and strut[6:8] == [34, 799] and sum(strut[:1] + strut[2:6]) == 0


def test_fit_leaves_fitting_windows_alone_and_moves_others_inside():
    area = (0, 34, 819, 766)
    assert layout.fit((100, 100, 400, 300), area) is None
    assert layout.fit((600, 100, 400, 300), area) == (407, 100, 400, 300)  # moved left, 12px from the column
    assert layout.fit((0, 0, 2000, 2000), area) == (12, 46, 795, 742)  # shrunk to the area minus the gap


def test_tabs_and_hits():
    assert layout.tab_labels(1) == ["Claude"]
    assert layout.tab_labels(3) == ["Claude 1", "Claude 2", "Claude 3"]
    boxes = layout.tab_boxes(["Claude 1", "Claude 2"], measure=lambda text: 50)
    assert boxes == [(14, 74), (92, 74)]
    assert [layout.hit(boxes, x) for x in (10, 20, 90, 100, 200)] == [None, 0, None, 1, None]


def test_chat_titles():
    assert layout.is_chat("Claude Code") and not layout.is_chat("Claude Code - notes.txt")


def test_wallpaper_paints_the_chat_column():
    pytest.importorskip("PIL", reason="Pillow is installed on Odd1 by module.nix")
    image = layout.wallpaper((1280, 800), 461)
    assert image.size == (1280, 800)
    assert image.getpixel((1000, 400)) == layout.CHAT_BACKGROUND
    assert image.getpixel((818, 400)) == layout.CHAT_EDGE
    assert image.getpixel((10, 0)) == layout.WALLPAPER[0] and image.getpixel((10, 799)) == layout.WALLPAPER[1]
