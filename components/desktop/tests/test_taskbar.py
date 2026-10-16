"""The taskbar's rules: what gets an item, where items sit, the space it reserves; icons; the desktop launchers."""
from odd1_desktop import layout, shell, taskbar


def test_one_item_per_app_then_one_for_all_agents_then_one_per_chat():
    apps = [(1, "Firefox", "Odd1 — Firefox"), (2, "Thunar", "agent - Thunar"), (3, "Firefox", "Docs — Firefox")]
    items = layout.taskbar_items(apps, (9, 3), ["chat-a", "chat-b"])
    assert items == [("app", "Firefox", "Firefox"), ("app", "Thunar", "agent - Thunar"),
                     ("agents", 9, "Agents · 3 apps"), ("chat", "chat-a", "Claude 1"), ("chat", "chat-b", "Claude 2")]


def test_agents_item_only_while_the_agent_desktop_exists_and_counts_singular():
    assert layout.taskbar_items([], (None, 5), []) == []
    assert layout.taskbar_items([], (7, 1), [])[0][2] == "Agents · 1 app"
    assert layout.taskbar_items([], (7, 0), [])[0][2] == "Agents"
    assert layout.taskbar_items([], (None, 0), ["c"]) == [("chat", "c", "Claude")]


def test_slots_separate_kinds_and_hits_find_them():
    items = [("app", "a", ""), ("app", "b", ""), ("agents", 1, ""), ("chat", "c", "")]
    slots, width = layout.taskbar_slots(items)
    icon, pad, gap, sep = layout.TASKBAR_ICON, layout.TASKBAR_PAD, layout.TASKBAR_GAP, layout.TASKBAR_SEPARATOR
    assert [x for x, _ in slots] == [pad, pad + icon + gap, pad + 2 * icon + gap + sep, pad + 3 * icon + gap + 2 * sep]
    assert width == slots[-1][0] + icon + pad
    assert layout.hit(slots, slots[2][0] + 1) == 2 and layout.hit(slots, slots[1][0] - 1) is None
    assert layout.taskbar_slots([])[1] == icon + 2 * pad  # an empty bar keeps its shape


def test_taskbar_floats_centred_in_the_work_area_and_reserves_the_bottom():
    screen, top = (1920, 1080), 34
    x, y, width, height = layout.taskbar_box(screen, top, 300)
    area_x, _, area_width, _ = layout.work_area(screen, top)
    assert x - area_x == area_x + area_width - (x + width)  # centred left of the chat column
    assert y + height + layout.TASKBAR_MARGIN == 1080
    strut = layout.taskbar_strut(screen, top)
    assert strut[3] == layout.taskbar_reserved() == height + 2 * layout.TASKBAR_MARGIN
    assert strut[10:12] == [area_x, area_x + area_width - 1]
    assert layout.work_area(screen, top, layout.taskbar_reserved())[3] == 1080 - 34 - layout.taskbar_reserved()


def test_net_wm_icon_picks_the_closest_size_at_least_as_big():
    small = [16, 16] + [0xFFFF0000] * 256  # opaque red
    large = [64, 64] + [0x8000FF00] * 4096  # half-transparent green
    icon = taskbar.net_wm_icon(small + large, 40)
    assert icon.size == (40, 40) and icon.getpixel((20, 20)) == (0, 255, 0, 128)
    assert taskbar.net_wm_icon(small, 40).getpixel((20, 20)) == (255, 0, 0, 255)  # only a small one: scaled up
    assert taskbar.net_wm_icon([], 40) is None and taskbar.net_wm_icon([5, 5, 1], 40) is None  # truncated


def test_generated_icons_are_rounded_tiles():
    for icon in (taskbar.agents_icon(40), taskbar.chat_icon(40)):
        assert icon.size == (40, 40) and icon.mode == "RGBA"
        assert icon.getpixel((0, 0))[3] == 0 and icon.getpixel((20, 4))[3] == 255  # corners cut, edges solid


def test_desktop_launchers_are_seeded_once_and_executable(tmp_path):
    apps = tmp_path / "applications"
    apps.mkdir()
    (apps / "firefox.desktop").write_text("[Desktop Entry]\nName=Firefox\n")
    home = tmp_path / "home"
    assert shell.seed_desktop(str(home), str(apps), {"firefox.desktop": {"Name": "Web"}, "missing.desktop": {}}) == [
        "firefox.desktop"]
    launcher = home / "Desktop" / "firefox.desktop"
    assert launcher.stat().st_mode & 0o111 and "Name=Web" in launcher.read_text()
    launcher.unlink()  # the person removed it: it stays removed
    assert shell.seed_desktop(str(home), str(apps), ("firefox.desktop",)) == [] and not launcher.exists()


def test_launcher_overrides_replace_keys_and_drop_their_translations():
    text = "[Desktop Entry]\nName=Thunar File Manager\nName[de]=Thunar\nExec=thunar\n[Desktop Action x]\nName=Keep\n"
    out = shell.launcher(text, {"Name": "Files"})
    assert out == "[Desktop Entry]\nName=Files\nExec=thunar\n[Desktop Action x]\nName=Keep\n"


def test_silence_turns_the_bell_volume_to_zero_and_reports_a_missing_display():
    calls = []

    class Screen:
        def change_keyboard_control(self, **values):
            calls.append(values)

        def sync(self):
            calls.append("sync")

    import ctypes.util
    import os
    libx11 = os.environ.get("ODD1_LIBX11") or ctypes.util.find_library("X11")
    if libx11 is None:
        import pytest
        pytest.skip("no libX11 here")
    assert shell.silence(Screen(), ":97", libx11) is False  # no such display: only the volume was set
    assert calls == [{"bell_percent": 0}, "sync"]
