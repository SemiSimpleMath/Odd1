# desktop: the person's desktop on any X display: Xfce's window manager and panel, themed, with the chat column on
# the right (odd1-desktop shell). `odd1.desktop.enable` switches the person's screen over to it (base.nix then
# runs Xvnc and Remote Desktop instead of Xvfb, openbox and x11vnc).
{ config, lib, pkgs, ... }:

let
  cfg = config.odd1.desktop;
  python = pkgs.python3.withPackages (p: [ p.xlib p.pillow p.pytest ]);
  tools = with pkgs; [ hsetroot xfce.xfce4-terminal fontconfig xfce.xfconf ];
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
    export PATH=${lib.makeBinPath tools}:$PATH
  '';
  cli = pkgs.writeShellScriptBin "odd1-desktop" ''
    ${env}
    exec ${python}/bin/python3 -m odd1_desktop.cli "$@"
  '';
  test = pkgs.writeShellScriptBin "odd1-desktop-test" ''
    ${env}
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
  # The session's pieces, each a user service on the person's display; they restart on their own.
  session = description: command: {
    inherit description;
    wantedBy = [ "default.target" ];
    after = [ "odd1-display.service" ];
    requires = [ "odd1-display.service" ];
    partOf = [ "odd1-display.service" ];
    environment = {
      DISPLAY = ":0";
      XDG_DATA_DIRS = "/run/current-system/sw/share";
      XDG_CONFIG_DIRS = "/etc/xdg:/run/current-system/sw/etc/xdg";
      PATH = lib.mkForce "/run/current-system/sw/bin:${lib.makeBinPath tools}";
    };
    serviceConfig = { ExecStart = command; Restart = "always"; RestartSec = 2; };
  };
  xfconf = name: { "xdg/xfce4/xfconf/xfce-perchannel-xml/${name}".source = ./xfconf + "/${name}"; };
in {
  options.odd1.desktop.enable = lib.mkEnableOption "the Odd1 desktop (Xfce, chat column, Remote Desktop) on the person's screen";

  config = lib.mkMerge [
    {
      environment.systemPackages = [ cli test ] ++ (with pkgs; [
        xfce.xfwm4 xfce.xfce4-panel xfce.xfce4-settings xfce.xfce4-whiskermenu-plugin xfce.xfce4-terminal
        xfce.exo xfce.garcon xfce.libxfce4ui xfce.xfce4-appfinder
        colloid-gtk-theme colloid-icon-theme adwaita-icon-theme hicolor-icon-theme
        hsetroot glib gtk3.out desktop-file-utils shared-mime-info
      ]);
      fonts.packages = with pkgs; [ inter jetbrains-mono ];
      programs.xfconf.enable = true;
      # Panel plugins and their data are found through the system profile, as NixOS's Xfce module does.
      environment.pathsToLink = [ "/share/xfce4" "/lib/xfce4" ];
    }
    (lib.mkIf cfg.enable {
      # The look: window manager, GTK theme and fonts (xsettings), the top bar, the chat terminal.
      environment.etc = xfconf "xfwm4.xml" // xfconf "xsettings.xml" // xfconf "xfce4-panel.xml"
        // xfconf "xfce4-terminal.xml";
      # Breathing room around terminal text (GTK reads it from the user's config only).
      systemd.user.tmpfiles.users.agent.rules = [ "L+ %h/.config/gtk-3.0/gtk.css - - - - ${./gtk.css}" ];
      fonts.fontconfig = {
        defaultFonts = { sansSerif = [ "Inter" ]; monospace = [ "JetBrains Mono" ]; };
        hinting.style = "slight";
        subpixel.rgba = "none";  # grayscale antialiasing: crisp through Remote Desktop's compression
      };
      systemd.user.services = {
        odd1-desktop-settings = session "Odd1 desktop: settings daemon (theme, fonts)"
          "${pkgs.xfce.xfce4-settings}/bin/xfsettingsd --replace --no-daemon --disable-wm-check";
        odd1-desktop-wm = session "Odd1 desktop: window manager" "${pkgs.xfce.xfwm4}/bin/xfwm4 --replace";
        odd1-desktop-panel = session "Odd1 desktop: top bar" "${pkgs.xfce.xfce4-panel}/bin/xfce4-panel";
        odd1-desktop-shell = session "Odd1 desktop: chat column, wallpaper, layout"
          "${cli}/bin/odd1-desktop shell --display :0";
      };
    })
  ];
}
