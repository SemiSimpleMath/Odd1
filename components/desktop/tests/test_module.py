"""The desktop module installs what the design needs and switches the session on only when enabled."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "module.nix").read_text()


def test_module_installs_window_manager_panel_theme_and_fonts():
    for package in ("xfce.xfwm4", "xfce.xfce4-panel", "xfce.xfce4-whiskermenu-plugin", "xfce.xfce4-windowck-plugin",
                    "colloid-gtk-theme",
                    "inter", "jetbrains-mono"):
        assert package in MODULE


def test_session_and_settings_only_when_enabled():
    enabled = MODULE[MODULE.index("lib.mkIf cfg.enable"):]
    for unit in ("odd1-desktop-settings", "odd1-desktop-wm", "odd1-desktop-panel", "odd1-desktop-icons",
                 "odd1-desktop-shell"):
        assert unit in enabled
    assert "map (channel: xfconf" in enabled and "programs.dconf" in enabled


def test_every_owned_channel_has_repository_defaults_and_is_reset_at_session_start():
    import re
    owned = re.search(r"channels = \[([^\]]*)\]", MODULE).group(1).split()
    assert {name.strip('"') for name in owned} == {path.stem for path in (ROOT / "xfconf").glob("*.xml")}
    assert "resetChannels" in MODULE[MODULE.index("odd1-desktop-settings ="):]


def test_xfconf_defaults_are_well_formed_and_name_their_channel():
    import xml.dom.minidom
    for path in (ROOT / "xfconf").glob("*.xml"):
        channel = xml.dom.minidom.parse(str(path)).documentElement
        assert channel.tagName == "channel" and channel.getAttribute("name") == path.stem
