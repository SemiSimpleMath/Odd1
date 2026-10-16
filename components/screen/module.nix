# screen: the `odd1-screen` CLI, its MCP server (agent entry point) and the odd1_screen package, built on any-app's
# readers; and the session setting that makes Firefox and Qt apps publish their accessibility trees.
{ pkgs, lib, ... }:

let
  python = pkgs.odd1Python.withPackages (p: [ p.pytest p.mcp p.pyatspi p.pygobject3 p.tkinter p.xlib p.pillow ]);
  tools = with pkgs; [ tesseract imagemagick xdotool wmctrl xorg.xprop xorg.xwininfo ];
  pythonpath = "${./.}:${../any-app}";
  # AT-SPI for reading apps, GTK 3 for the consent dialog (consent.py).
  typelibs = lib.makeSearchPath "lib/girepository-1.0" (map lib.getLib (with pkgs; [
    at-spi2-core glib gtk3 pango gdk-pixbuf atk harfbuzz gobject-introspection ]));
  env = ''
    export PYTHONPATH=${pythonpath}''${PYTHONPATH:+:$PYTHONPATH}
    export PATH=${pkgs.lib.makeBinPath tools}:$PATH
    export GI_TYPELIB_PATH=${typelibs}''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}
    export DISPLAY=''${DISPLAY:-:0}
  '';
  cli = pkgs.writeShellScriptBin "odd1-screen" ''
    ${env}
    exec ${python}/bin/python3 -m odd1_screen.cli "$@"
  '';
  server = pkgs.writeShellScriptBin "odd1-screen-mcp" ''
    ${env}
    exec ${python}/bin/python3 -m odd1_screen.mcp_server "$@"
  '';
  test = pkgs.writeShellScriptBin "odd1-screen-test" ''
    ${env}
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
in {
  environment.systemPackages = [ cli server test ] ++ tools;
  # The agent entry point (Odd1 convention): registered for every agent on the machine.
  odd1.mcpServers.screen = { command = "${server}/bin/odd1-screen-mcp"; args = [ ]; };
  # Firefox publishes its page (DOM) over AT-SPI only when asked; Qt likewise. Then web pages and Qt apps join the
  # screen model like GTK ones. Applies to programs started after login (or with these in their environment).
  environment.sessionVariables = { GNOME_ACCESSIBILITY = "1"; QT_ACCESSIBILITY = "1"; };
  environment.variables.ODD1_SCREEN_PYTHONPATH = pythonpath;
}
