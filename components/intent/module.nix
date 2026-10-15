# odd1-intent (CLI), odd1-intent-gui (Tk window), the intent MCP server, and an on-demand user unit for the window.
{ pkgs, ... }:

let
  python = pkgs.python3.withPackages (p: [ p.tkinter p.pytest p.mcp ]);
  state = "/var/lib/odd1/intent";
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
    export ODD1_INTENT_STATE=''${ODD1_INTENT_STATE:-${state}}
  '';
  cli = pkgs.writeShellScriptBin "odd1-intent" ''
    ${env}
    exec ${python}/bin/python3 -m odd1_intent.cli "$@"
  '';
  gui = pkgs.writeShellScriptBin "odd1-intent-gui" ''
    ${env}
    export DISPLAY=''${DISPLAY:-:0}
    exec ${python}/bin/python3 -m odd1_intent.gui "$@"
  '';
  server = pkgs.writeShellScriptBin "odd1-intent-mcp" ''
    ${env}
    exec ${python}/bin/python3 -m odd1_intent.mcp_server "$@"
  '';
  test = pkgs.writeShellScriptBin "odd1-intent-test" ''
    export PYTHONPATH=${./.}
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
in {
  environment.systemPackages = [ cli gui server test ];
  # The agent entry point (Odd1 convention): registered for every agent on the machine.
  odd1.mcpServers.intent = { command = "${server}/bin/odd1-intent-mcp"; args = [ ]; };
  # History, undo backups and the engine lock; private to the agent user (the engine checks ownership).
  systemd.tmpfiles.rules = [ "d ${state} 0700 agent users -" ];
  # The window on the desktop (DISPLAY :0). Not started at login: `systemctl --user start odd1-intent-gui`.
  systemd.user.services.odd1-intent-gui = {
    description = "Intent: preview-first file actions window";
    environment = { DISPLAY = ":0"; ODD1_INTENT_STATE = state; };
    serviceConfig = {
      ExecStart = "${gui}/bin/odd1-intent-gui";
      Restart = "on-failure";
      RestartSec = 3;
    };
  };
}
