# snip: a snipping tool like Windows' (odd1-snip): Print or Super+Shift+S freezes the screen, the person snips a part,
# it is copied to the clipboard (Remote Desktop carries it to Windows), saved to ~/Pictures/Screenshots and opened
# for marking up.
{ config, pkgs, lib, ... }:

let
  python = pkgs.odd1Python.withPackages (p: [ p.pytest p.pygobject3 p.pycairo p.xlib ]);
  typelibs = lib.makeSearchPath "lib/girepository-1.0" (map lib.getLib (with pkgs; [
    gtk3 glib pango gdk-pixbuf atk harfbuzz gobject-introspection ]));
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
    export GI_TYPELIB_PATH=${typelibs}''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}
  '';
  cli = pkgs.writeShellScriptBin "odd1-snip" ''
    ${env}
    exec ${python}/bin/python3 -m odd1_snip.cli "$@"
  '';
  test = pkgs.writeShellScriptBin "odd1-snip-test" ''
    ${env}
    export PYTHONDONTWRITEBYTECODE=1
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
  launcher = pkgs.makeDesktopItem {
    name = "odd1-snip";
    desktopName = "Snipping Tool";
    genericName = "Screenshot";
    comment = "Snip part of the screen: copy it, mark it up, save it (Print, Super+Shift+S)";
    exec = "${cli}/bin/odd1-snip";
    icon = "applets-screenshooter";
    categories = [ "Utility" ];
    keywords = [ "screenshot" "snip" "capture" "print screen" ];
  };
  # Xfce's own bindings for these keys run xfce4-screenshooter, which Odd1 does not install. The channel is Xfce's
  # (the desktop does not own it), so set just these keys at every session start; xfsettingsd picks them up live.
  keys = {
    "Print" = "odd1-snip";
    "<Shift>Print" = "odd1-snip";
    "<Shift><Super>s" = "odd1-snip";
    "<Alt>Print" = "odd1-snip --mode window";
  };
  bindKeys = pkgs.writeShellScript "odd1-snip-keys" (lib.concatStrings (lib.mapAttrsToList (key: command: ''
    ${pkgs.xfce.xfconf}/bin/xfconf-query -c xfce4-keyboard-shortcuts -p '/commands/custom/${key}' -n -t string -s '${command}'
  '') keys));
in {
  config = lib.mkMerge [
    { environment.systemPackages = [ cli test launcher ]; }
    (lib.mkIf config.odd1.desktop.enable {
      systemd.user.services.odd1-snip-keys = {
        description = "Odd1 snip: Print and Super+Shift+S start the snipping tool";
        wantedBy = [ "default.target" ];
        after = [ "odd1-desktop-settings.service" ];
        requires = [ "odd1-desktop-settings.service" ];
        partOf = [ "odd1-display.service" ];
        environment.DISPLAY = ":0";
        serviceConfig = { Type = "oneshot"; RemainAfterExit = true; ExecStart = "${bindKeys}"; };
      };
    })
  ];
}
