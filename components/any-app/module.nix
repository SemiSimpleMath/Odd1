# any-app: the `any-app` CLI, its MCP server (agent entry point) and the any_app Python package.
{ pkgs, ... }:

let
  python = pkgs.python3.withPackages (p: [ p.pytest p.mcp p.pyatspi p.pygobject3 p.tkinter ]);
  tools = with pkgs; [ tesseract imagemagick wmctrl xdotool ];
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
    export PATH=${pkgs.lib.makeBinPath tools}:$PATH
    export GI_TYPELIB_PATH=${pkgs.at-spi2-core}/lib/girepository-1.0''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}
    export DISPLAY=''${DISPLAY:-:0}
  '';
  cli = pkgs.writeShellScriptBin "odd1-any-app" ''
    ${env}
    exec ${python}/bin/python3 -m any_app.cli "$@"
  '';
  server = pkgs.writeShellScriptBin "odd1-any-app-mcp" ''
    ${env}
    exec ${python}/bin/python3 -m any_app.mcp_server "$@"
  '';
  test = pkgs.writeShellScriptBin "odd1-any-app-test" ''
    ${env}
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
in {
  environment.systemPackages = [ cli server test ] ++ tools;
  # The agent entry point (Odd1 convention): registered for every agent on the machine.
  odd1.mcpServers.any-app = { command = "${server}/bin/odd1-any-app-mcp"; args = [ ]; };
  # For agents' own scripts: PYTHONPATH=$ODD1_ANY_APP_PYTHONPATH python3 -c 'from any_app import describe_pixels'
  environment.variables.ODD1_ANY_APP_PYTHONPATH = "${./.}";
}
