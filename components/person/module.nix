# person: the person's own settings (About you; odd1-person). `odd1.person` holds what the system is installed with
# (their name and email: the menu, git); their own changes live in ~/.config/odd1/person.json and win.
{ config, pkgs, lib, ... }:

let
  cfg = config.odd1.person;
  python = pkgs.odd1Python.withPackages (p: [ p.pytest p.pygobject3 ]);
  typelibs = lib.makeSearchPath "lib/girepository-1.0" (map lib.getLib (with pkgs; [
    gtk4 libadwaita glib pango gdk-pixbuf graphene harfbuzz gobject-introspection ]));
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
    export GI_TYPELIB_PATH=${typelibs}''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}
    # Xvnc has no GL: GTK 4 draws with cairo.
    export GSK_RENDERER=''${GSK_RENDERER:-cairo}
  '';
  cli = pkgs.writeShellScriptBin "odd1-person" ''
    ${env}
    exec ${python}/bin/python3 -m odd1_person.cli "$@"
  '';
  test = pkgs.writeShellScriptBin "odd1-person-test" ''
    ${env}
    export PYTHONDONTWRITEBYTECODE=1
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
  launcher = pkgs.makeDesktopItem {
    name = "odd1-person";
    desktopName = "About you";
    comment = "Your name and how agents should address you";
    exec = "${cli}/bin/odd1-person settings";
    icon = "avatar-default";
    categories = [ "Settings" ];
  };
in {
  options.odd1.person = {
    name = lib.mkOption { type = lib.types.str; default = ""; description = "The person's full name."; };
    email = lib.mkOption { type = lib.types.str; default = ""; description = "The person's email (their commits)."; };
  };
  config = {
    environment.systemPackages = [ cli test launcher ];
    environment.etc."odd1/person.json".text = builtins.toJSON { inherit (cfg) name email; };
    # The menu's header and anything else that asks the system for the user's real name.
    users.users.agent.description = cfg.name;
    # Commits made here carry the person's name (ticket 22); About you also sets it in ~/.gitconfig when it changes.
    programs.git.config.user = lib.mkIf (cfg.name != "") { inherit (cfg) name email; };
  };
}
