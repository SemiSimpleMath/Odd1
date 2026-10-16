# odd1-agents: the Agents app (GTK 4, libadwaita) and `odd1-agents status`; it reads and changes the task manager
# (components/tasks) and the caretaker's state, so both are on its PYTHONPATH and in its environment.
{ pkgs, lib, ... }:

let
  python = pkgs.python3.withPackages (p: [ p.pytest p.pygobject3 ]);
  typelibs = lib.makeSearchPath "lib/girepository-1.0" (map lib.getLib (with pkgs; [
    gtk4 libadwaita glib pango gdk-pixbuf graphene harfbuzz gobject-introspection ]));
  env = ''
    export PYTHONPATH=${./.}:${../tasks}''${PYTHONPATH:+:$PYTHONPATH}
    export GI_TYPELIB_PATH=${typelibs}''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}
    export ODD1_TASKS_STATE=''${ODD1_TASKS_STATE:-/var/lib/odd1/tasks}
    export DISPLAY=''${DISPLAY:-:0}
    # Xvnc has no GL: draw with cairo (the default GL renderer falls back slowly, or not at all, there).
    export GSK_RENDERER=''${GSK_RENDERER:-cairo}
  '';
  cli = pkgs.writeShellScriptBin "odd1-agents" ''
    ${env}
    exec ${python}/bin/python3 -m odd1_agents.cli "$@"
  '';
  test = pkgs.writeShellScriptBin "odd1-agents-test" ''
    ${env}
    export PYTHONDONTWRITEBYTECODE=1
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
  # In the Odd1 menu (and its Favorites, desktop/xfconf/xfce4-panel.xml) as "Agents".
  launcher = pkgs.makeDesktopItem {
    name = "odd1-agents";
    desktopName = "Agents";
    comment = "Health and settings of every agent: roles, tasks, Claude Code";
    exec = "${cli}/bin/odd1-agents";
    icon = "odd1";
    categories = [ "System" "Settings" ];
  };
in {
  environment.systemPackages = [ cli test launcher ];
}
