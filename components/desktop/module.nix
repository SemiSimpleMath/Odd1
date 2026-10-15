# desktop: the person's desktop on any X display: Xfce's window manager and panel, themed, with the Claude chat
# docked on the right. Packages and system defaults; the session units come with the Remote Desktop switch.
{ pkgs, ... }:

{
  environment.systemPackages = with pkgs; [
    xfce.xfwm4 xfce.xfce4-panel xfce.xfce4-settings xfce.xfce4-whiskermenu-plugin xfce.xfce4-terminal
    xfce.exo xfce.garcon xfce.libxfce4ui xfce.xfce4-appfinder
    colloid-gtk-theme colloid-icon-theme adwaita-icon-theme hicolor-icon-theme
    hsetroot glib gtk3.out desktop-file-utils shared-mime-info
  ];
  fonts.packages = with pkgs; [ inter jetbrains-mono ];
  programs.xfconf.enable = true;
  # Panel plugins and their data are found through the system profile, as NixOS's Xfce module does.
  environment.pathsToLink = [ "/share/xfce4" "/lib/xfce4" ];
}
