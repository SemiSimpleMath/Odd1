# desktop: the person's desktop on any X display: Xfce's window manager and panel, themed, with the chat column on
# the right (odd1-desktop shell). `odd1.desktop.enable` switches the person's screen over to it (base.nix then
# runs Xvnc and Remote Desktop instead of Xvfb, openbox and x11vnc).
{ config, lib, pkgs, ... }:

let
  cfg = config.odd1.desktop;
  python = pkgs.odd1Python.withPackages (p: [ p.xlib p.pillow p.pytest ]);
  tools = with pkgs; [ hsetroot xfce.xfce4-terminal fontconfig xfce.xfconf librsvg ];
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
    export ODD1_MARK=${./logo/odd1-icon.svg}  # the owl, faint on the wallpaper
    export ODD1_LIBX11=${pkgs.xorg.libX11}/lib/libX11.so.6  # the shell turns XKB's audible bell off through it
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
  afterSettings = unit: unit // { after = unit.after ++ [ "odd1-desktop-settings.service" ]; };
  # The Odd1 owl (logo/odd1-icon.svg, the owl from logo/odd1-logo.svg) as the icon `odd1`: the menu button shows it.
  icon = pkgs.runCommand "odd1-icon" { } "install -Dm644 ${./logo/odd1-icon.svg} $out/share/icons/hicolor/scalable/apps/odd1.svg";
  # Window frames as dark as the top bar, whatever the GTK theme: Colloid-Dark's xfwm4 images, with the colours they
  # would take from the (light) GTK theme (`c <colour> s active_color_2`, ...) fixed to the bar's.
  frames = pkgs.runCommand "odd1-xfwm4-theme" { } ''
    mkdir -p $out/share/themes/Odd1
    cp -rL ${pkgs.colloid-gtk-theme}/share/themes/Colloid-Dark/xfwm4 $out/share/themes/Odd1/  # files, not its links
    chmod -R u+w $out/share/themes/Odd1
    cd $out/share/themes/Odd1/xfwm4
    sed -i -e 's/c [^ ]* s active_color_[0-9]*/c #181A1D/' -e 's/c [^ ]* s inactive_color_[0-9]*/c #202227/' \
      -e 's/\(c [^ ]*\) s [a-z_0-9]*/\1/' *.xpm
    sed -i -e 's/^active_text_color=.*/active_text_color=#E4E4E8/' -e 's/^inactive_text_color=.*/inactive_text_color=#8A8C96/' \
      -e 's/^active_text_shadow_color=.*/active_text_shadow_color=#181A1D/' \
      -e 's/^inactive_text_shadow_color=.*/inactive_text_shadow_color=#202227/' themerc
    # The same buttons for the maximized window's controls in the top bar (xfce4-windowck-plugin reads unity/).
    mkdir ../unity
    for state in unfocused:inactive focused_normal:active focused_prelight:prelight focused_pressed:pressed; do
      for button in minimize:hide maximize:maximize unmaximize:maximize-toggled close:close; do
        ln -s ../xfwm4/''${button#*:}-''${state#*:}.svg ../unity/''${button%:*}_''${state%:*}.svg
      done
    done
  '';
  xfconf = name: { "xdg/xfce4/xfconf/xfce-perchannel-xml/${name}".source = ./xfconf + "/${name}"; };
  # Odd1 owns these settings channels: the repository's defaults (xfconf/) apply at every session start, so a value
  # Xfce once wrote into ~/.config (first-run defaults, an old layout) never shadows them. xfconfd restarts on demand.
  channels = [ "xfwm4" "xsettings" "xfce4-panel" "xfce4-desktop" "xfce4-terminal" ];
  resetChannels = pkgs.writeShellScript "odd1-desktop-reset-channels" ''
    for channel in ${lib.concatStringsSep " " channels}; do
      rm -f "$HOME/.config/xfce4/xfconf/xfce-perchannel-xml/$channel.xml"
    done
    ${pkgs.procps}/bin/pkill -x xfconfd || true
  '';
in {
  options.odd1.desktop.enable = lib.mkEnableOption "the Odd1 desktop (Xfce, chat column, Remote Desktop) on the person's screen";

  config = lib.mkMerge [
    {
      environment.systemPackages = [ cli test icon frames ] ++ (with pkgs; [
        xfce.xfwm4 xfce.xfce4-panel xfce.xfce4-settings xfce.xfce4-whiskermenu-plugin xfce.xfce4-windowck-plugin
        xfce.xfce4-terminal
        xfce.exo xfce.garcon xfce.libxfce4ui xfce.xfce4-appfinder xfce.xfdesktop xfce.thunar
        colloid-gtk-theme colloid-icon-theme adwaita-icon-theme hicolor-icon-theme
        hsetroot glib gtk3.out desktop-file-utils shared-mime-info
        xclip  # Claude Code reads pasted images from the clipboard with it
      ]);
      fonts.packages = with pkgs; [ inter jetbrains-mono ];
      programs.xfconf.enable = true;
      # Panel plugins and their data are found through the system profile, as NixOS's Xfce module does.
      environment.pathsToLink = [ "/share/xfce4" "/lib/xfce4" ];
    }
    (lib.mkIf cfg.enable {
      # The look: window manager, GTK theme and fonts (xsettings), the top bar, the chat terminal.
      environment.etc = lib.mkMerge (map (channel: xfconf "${channel}.xml") channels);
      # The same look for GSettings: xfsettingsd copies GNOME's interface settings into xsettings at start (without
      # these it copies GNOME's defaults, Adwaita), and GTK4 apps read them directly.
      programs.dconf = {
        enable = true;
        profiles.user.databases = [{
          settings."org/gnome/desktop/interface" = {
            gtk-theme = "Colloid-Light"; icon-theme = "Colloid-Light"; cursor-theme = "Adwaita";
            cursor-size = lib.gvariant.mkInt32 24; font-name = "Inter 10"; document-font-name = "Inter 10";
            monospace-font-name = "JetBrains Mono 10"; color-scheme = "prefer-light";
          };
        }];
      };
      # Desktop notifications (agents' escalations reach the person this way): xfce4-notifyd, started on demand by
      # D-Bus when a program first sends one (org.freedesktop.Notifications).
      environment.systemPackages = [ pkgs.xfce.xfce4-notifyd pkgs.libnotify ];
      services.dbus.packages = [ pkgs.xfce.xfce4-notifyd ];
      systemd.packages = [ pkgs.xfce.xfce4-notifyd ];
      # Breathing room around terminal text (GTK reads it from the user's config only).
      systemd.user.tmpfiles.users.agent.rules = [ "L+ %h/.config/gtk-3.0/gtk.css - - - - ${./gtk.css}" ];
      fonts.fontconfig = {
        defaultFonts = { sansSerif = [ "Inter" ]; monospace = [ "JetBrains Mono" ]; };
        hinting.style = "slight";
        subpixel.rgba = "none";  # grayscale antialiasing: crisp through Remote Desktop's compression
      };
      systemd.user.services = {
        odd1-desktop-settings = lib.recursiveUpdate (session "Odd1 desktop: settings daemon (theme, fonts)"
          "${pkgs.xfce.xfce4-settings}/bin/xfsettingsd --replace --no-daemon --disable-wm-check")
          { serviceConfig.ExecStartPre = "${resetChannels}"; };
        odd1-desktop-wm = afterSettings (session "Odd1 desktop: window manager" "${pkgs.xfce.xfwm4}/bin/xfwm4 --replace");
        odd1-desktop-panel = afterSettings (session "Odd1 desktop: top bar" "${pkgs.xfce.xfce4-panel}/bin/xfce4-panel");
        odd1-desktop-icons = afterSettings (session "Odd1 desktop: wallpaper and desktop icons"
          "${pkgs.xfce.xfdesktop}/bin/xfdesktop");
        # The clipboard over Remote Desktop, images included, and the viewer's shared drives at ~/Windows: xrdp's
        # channel server on :0 (base.nix points xrdp at its socket). It waits for each connection. FUSE mounts need
        # the setuid fusermount3 in /run/wrappers.
        odd1-desktop-clipboard = lib.recursiveUpdate (session
          "Odd1 desktop: clipboard and shared drives over Remote Desktop (xrdp-chansrv)" "${pkgs.xrdp}/bin/xrdp-chansrv")
          { environment.PATH = lib.mkForce "/run/wrappers/bin:/run/current-system/sw/bin:${lib.makeBinPath tools}"; };
        # Stopping the shell stops only the shell: chats it opened (and the person's conversations) live on.
        odd1-desktop-shell = lib.recursiveUpdate (afterSettings (session
          "Odd1 desktop: chat column, taskbar, wallpaper, layout" "${cli}/bin/odd1-desktop shell --display :0"))
          { serviceConfig.KillMode = "process"; };
      };
    })
  ];
}
