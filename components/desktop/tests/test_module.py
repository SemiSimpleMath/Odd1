"""The desktop module installs the pieces the design is built from."""
from pathlib import Path

MODULE = (Path(__file__).resolve().parents[1] / "module.nix").read_text()


def test_module_installs_window_manager_panel_theme_and_fonts():
    for package in ("xfce.xfwm4", "xfce.xfce4-panel", "xfce.xfce4-whiskermenu-plugin", "colloid-gtk-theme",
                    "inter", "jetbrains-mono"):
        assert package in MODULE


def test_panel_plugins_are_linked_into_the_system_profile():
    assert '"/lib/xfce4"' in MODULE and "programs.xfconf.enable = true" in MODULE
