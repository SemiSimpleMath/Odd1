"""The desktop's geometry rules: the chat column, the work area, fitting windows, tabs, the wallpaper."""
import pytest

from odd1_desktop import layout


def test_chat_column_is_about_a_third_within_bounds():
    assert layout.dock_width(1280) == 461
    assert layout.dock_width(1920) == 680  # capped: never dominant
    assert layout.dock_width(1024) == 460  # floored: never cramped


def test_column_header_chat_and_work_area_tile_the_screen():
    screen, top = (1920, 1080), 34
    x, y, width, height = layout.column(screen)
    assert (x + width, y, y + height) == (1920, 0, 1080)  # full height: the chat tabs are at the top of the screen
    assert layout.header_box(screen) == (x, 0, width, layout.HEADER_HEIGHT)
    assert layout.chat_box(screen) == (x, layout.HEADER_HEIGHT, width, height - layout.HEADER_HEIGHT)
    assert layout.work_area(screen, top) == (0, 34, x, 1046)


def test_top_bar_spans_exactly_the_work_area():
    for width in (1024, 1280, 1920, 2560):
        assert round(layout.panel_length(width) * width / 100) == width - layout.dock_width(width)


def test_strut_reserves_the_column_on_the_right_top_to_bottom():
    strut = layout.strut((1280, 800))
    assert strut[1] == 461 and strut[6:8] == [0, 799] and sum(strut[:1] + strut[2:6]) == 0


def test_fit_leaves_fitting_windows_alone_and_moves_others_inside():
    area = (0, 34, 819, 766)
    assert layout.fit((100, 100, 400, 300), area) is None
    assert layout.fit((600, 100, 400, 300), area) == (407, 100, 400, 300)  # moved left, 12px from the column
    assert layout.fit((0, 0, 2000, 2000), area) == (12, 46, 795, 742)  # shrunk to the area minus the gap


def test_tabs_and_hits():
    assert layout.tab_labels(["Claude"]) == ["Claude"]
    assert layout.tab_labels(["Claude"] * 3) == ["Claude 1", "Claude 2", "Claude 3"]
    assert layout.tab_labels(["Claude", "Task manager", "Claude"]) == ["Claude 1", "Task manager", "Claude 2"]
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
    # The work area is dark (never a blinding white) and starts from the base colour where no field reaches.
    assert all(max(image.getpixel((x, y))) < 150 for x in range(0, 818, 37) for y in range(0, 800, 41))
    assert all(abs(a - b) <= 6 for a, b in zip(image.getpixel((10, 0)), layout.WALLPAPER[0]))


def test_watermark_is_a_faint_owl_in_the_middle_of_the_work_area():
    pytest.importorskip("PIL")
    from PIL import Image
    mark = Image.new("RGBA", (100, 100), (248, 245, 242, 255))  # a light square stands in for the owl's face
    plain = layout.wallpaper((1280, 800), 461)
    marked = layout.wallpaper((1280, 800), 461, mark)
    centre = ((1280 - 461) // 2, 800 // 2 - 800 // 36)
    dimmed = 248 * layout.WATERMARK_BRIGHTNESS  # the face, darkened, then mixed into the night at the opacity
    expected = plain.getpixel(centre)[0] * (1 - layout.WATERMARK_OPACITY) + dimmed * layout.WATERMARK_OPACITY
    assert abs(marked.getpixel(centre)[0] - expected) <= 3
    assert marked.getpixel((20, 20)) == plain.getpixel((20, 20))  # nowhere else
    assert layout.watermark_height(1440) == 562


def test_a_chat_names_its_agent_system_in_its_window_role():
    assert layout.agent_name("odd1-agent:tasks") == "tasks"
    assert layout.agent_name("odd1-agent:") is None
    assert layout.agent_name("xfce4-terminal-1234") is None and layout.agent_name("") is None


def test_new_chat_menu_hangs_under_its_button_inside_the_column():
    header = layout.header_box((2560, 1440))  # x 1880, 680 wide
    x, y, width, height = layout.menu_box(header, (580, 88), 2)
    assert (x + width, y, height) == (1880 + 580 + 88, layout.HEADER_HEIGHT, 2 * layout.MENU_ITEM + 2 * layout.MENU_PAD)
    assert layout.menu_box(header, (10, 88), 2)[0] == 1880 + 4  # never left of the column
    pad, item = layout.MENU_PAD, layout.MENU_ITEM
    assert [layout.menu_hit(y, 2) for y in (0, pad, pad + item - 1, pad + item, pad + 2 * item)] == [None, 0, 0, 1, None]


def test_a_new_chat_takes_the_focus_only_when_the_person_just_asked_for_it():
    assert layout.person_asked(100.0, 100.0 + layout.ASKED_S)
    assert not layout.person_asked(100.0, 100.0 + layout.ASKED_S + 1)  # too long ago: it waits as a tab
    assert not layout.person_asked(0.0, 5.0)  # never asked


def test_urgency_comes_from_the_hint_or_demands_attention():
    assert layout.urgent(layout.URGENCY_HINT | 1, [], 99)
    assert layout.urgent(0, [5, 99], 99)
    assert not layout.urgent(1, [5], 99)


def test_a_waiting_tab_is_wider_by_room_for_the_ball():
    plain = layout.tab_boxes(["A", "B"], lambda t: 10)
    waiting = layout.tab_boxes(["A", "B"], lambda t: 10, extra=[0, layout.ATTENTION_ROOM])
    assert waiting[0] == plain[0] and waiting[1][1] == plain[1][1] + layout.ATTENTION_ROOM


def test_a_soft_shadow_falls_on_the_wallpaper_beside_the_column():
    pytest.importorskip("PIL")
    plain_edge = layout.COLUMN_SHADOW_DEPTH
    image = layout.wallpaper((1280, 800), 461)
    edge = 1280 - 461
    near, far = sum(image.getpixel((edge - 2, 400))), sum(image.getpixel((edge - layout.COLUMN_SHADOW - 4, 400)))
    assert near < far * (1 - plain_edge / 2)  # darkest against the column, gone a shadow's width away
