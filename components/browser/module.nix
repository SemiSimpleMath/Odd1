# odd1-browser (CLI), its MCP server `browser`, and the odd1_browser package, with Firefox and geckodriver from nixpkgs.
{ pkgs, ... }:

let
  python = pkgs.python3.withPackages (p: [ p.selenium p.jsonschema p.pytest p.mcp ]);
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
    export PATH=${pkgs.lib.makeBinPath [ pkgs.firefox pkgs.geckodriver ]}:$PATH
  '';
  cli = pkgs.writeShellScriptBin "odd1-browser" ''
    ${env}
    exec ${python}/bin/python3 -m odd1_browser.cli "$@"
  '';
  server = pkgs.writeShellScriptBin "odd1-browser-mcp" ''
    ${env}
    exec ${python}/bin/python3 -m odd1_browser.mcp_server "$@"
  '';
  test = pkgs.writeShellScriptBin "odd1-browser-test" ''
    ${env}
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
in {
  environment.systemPackages = [ cli server test pkgs.geckodriver ];
  # The agent entry point (Odd1 convention): registered for every agent on the machine. Headless by default;
  # args = [ "--visible" ] would show Firefox on the desktop (DISPLAY :0) for people to watch.
  odd1.mcpServers.browser = { command = "${server}/bin/odd1-browser-mcp"; args = [ ]; };
}
