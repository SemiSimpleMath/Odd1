"""The desktop module installs what the design needs and switches the session on only when enabled."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "module.nix").read_text()


def test_module_installs_window_manager_panel_theme_and_fonts():
    for package in ("xfce.xfwm4", "xfce.xfce4-panel", "xfce.xfce4-whiskermenu-plugin", "colloid-gtk-theme",
                    "inter", "jetbrains-mono"):
        assert package in MODULE


def test_session_and_settings_only_when_enabled():
    enabled = MODULE[MODULE.index("lib.mkIf cfg.enable"):]
    for unit in ("odd1-desktop-settings", "odd1-desktop-wm", "odd1-desktop-panel", "odd1-desktop-shell"):
        assert unit in enabled
    for channel in ("xfwm4.xml", "xsettings.xml", "xfce4-panel.xml", "xfce4-terminal.xml"):
        assert channel in enabled and (ROOT / "xfconf" / channel).exists()


def test_xfconf_defaults_are_well_formed_and_name_their_channel():
    import xml.dom.minidom
    for path in (ROOT / "xfconf").glob("*.xml"):
        channel = xml.dom.minidom.parse(str(path)).documentElement
        assert channel.tagName == "channel" and channel.getAttribute("name") == path.stem
