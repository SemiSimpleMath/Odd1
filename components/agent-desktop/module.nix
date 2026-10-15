# agent-desktop: a second X display (Xephyr :1 with openbox) where agents run apps out of the person's way; shown as
# one window on the person's screen, minimized at start. The odd1-agent-desktop CLI and MCP server start apps there.
{ pkgs, ... }:

let
  python = pkgs.python3.withPackages (p: [ p.pytest p.mcp p.tkinter ]);  # tkinter: the live test's probe window
  tools = with pkgs; [ wmctrl xdotool xorg.xdpyinfo xorg.xprop ];
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
    export PATH=$PATH:${pkgs.lib.makeBinPath tools}
  '';
  cli = pkgs.writeShellScriptBin "odd1-agent-desktop" ''
    ${env}
    exec ${python}/bin/python3 -m odd1_agent_desktop.cli "$@"
  '';
  server = pkgs.writeShellScriptBin "odd1-agent-desktop-mcp" ''
    ${env}
    exec ${python}/bin/python3 -m odd1_agent_desktop.mcp_server "$@"
  '';
  test = pkgs.writeShellScriptBin "odd1-agent-desktop-test" ''
    ${env}
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
  waitForAgentDisplay = pkgs.writeShellScript "odd1-agent-desktop-wait" ''
    for _ in $(seq 50); do DISPLAY=:1 ${pkgs.xorg.xdpyinfo}/bin/xdpyinfo >/dev/null 2>&1 && exit 0; sleep 0.2; done
    exit 1
  '';
in {
  environment.systemPackages = [ cli server test ] ++ tools;
  # The agent entry point (Odd1 convention): registered for every agent on the machine.
  odd1.mcpServers.agent-desktop = { command = "${server}/bin/odd1-agent-desktop-mcp"; args = [ ]; };
  environment.variables.ODD1_AGENT_DISPLAY = ":1";

  # The display itself: a window on the person's screen (:0) that is a whole X server (:1). 1280x720 so it fits on
  # the person's screen with its title bar when shown. Minimized once its window manager is up (below).
  systemd.user.services.odd1-agent-desktop = {
    description = "Odd1 agent desktop (Xephyr :1 on :0)";
    wantedBy = [ "default.target" ];
    after = [ "orbit-wm.service" ];
    environment.DISPLAY = ":0";
    serviceConfig = {
      ExecStart = "${pkgs.xorg.xorgserver}/bin/Xephyr :1 -screen 1280x720 -title \"Agent desktop\" -nolisten tcp -br";
      Restart = "always";
      RestartSec = 2;
    };
  };
  # Its window manager, so apps there are managed (stacking, focus, minimize) like on the person's screen.
  systemd.user.services.odd1-agent-desktop-wm = {
    description = "Odd1 agent desktop window manager (openbox on :1)";
    wantedBy = [ "default.target" ];
    bindsTo = [ "odd1-agent-desktop.service" ];
    after = [ "odd1-agent-desktop.service" ];
    environment.DISPLAY = ":1";
    serviceConfig = {
      ExecStartPre = "${waitForAgentDisplay}";
      ExecStart = "${pkgs.openbox}/bin/openbox";
      # Xephyr shows its window again when a window manager starts inside it, so minimize it after openbox is up.
      ExecStartPost = "${pkgs.bash}/bin/bash -c 'sleep 2; ${cli}/bin/odd1-agent-desktop hide --wait 10'";
      Restart = "always";
      RestartSec = 2;
    };
  };
  # Apps agents start there run in this slice: they yield CPU and IO to the person's own programs.
  systemd.user.slices.odd1-agent = {
    description = "Programs agents run on the agent desktop";
    sliceConfig = { CPUWeight = 20; IOWeight = 20; };
  };
}
