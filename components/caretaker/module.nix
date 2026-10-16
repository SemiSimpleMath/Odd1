# The caretaker: its daemon (odd1-caretaker), CLI, MCP server and tests. State under /var/lib/odd1/caretaker.
{ config, pkgs, lib, ... }:

let
  # pillow draws the top bar's health icon, pygobject3 + GTK 3 its drop-down (health.py, popup.py).
  python = pkgs.odd1Python.withPackages (p: [ p.pytest p.mcp p.pillow p.pygobject3 ]);
  tools = [ pkgs.systemd pkgs.xdotool pkgs.coreutils ];
  typelibs = lib.makeSearchPath "lib/girepository-1.0" (map lib.getLib (with pkgs; [
    gtk3 glib pango gdk-pixbuf harfbuzz atk gobject-introspection ]));
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
    export GI_TYPELIB_PATH=${typelibs}''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}
    export PATH=${lib.makeBinPath tools}:/run/wrappers/bin:$PATH
    export DISPLAY=''${DISPLAY:-:0}
  '';
  wrap = name: module: pkgs.writeShellScriptBin name ''
    ${env}
    exec ${python}/bin/python3 -m ${module} "$@"
  '';
  cli = wrap "odd1-caretaker" "odd1_caretaker.cli";
  server = wrap "odd1-caretaker-mcp" "odd1_caretaker.mcp_server";
  test = pkgs.writeShellScriptBin "odd1-caretaker-test" ''
    ${env}
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
in {
  options.odd1.caretaker.restartUnits = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [ ];
    example = [ "nginx.service" ];
    description = "Units the caretaker restarts on its own when they fail (at most 3 times an hour each).";
  };

  config = {
    environment.systemPackages = [ cli server test ];
    # The agent entry point (Odd1 convention): caretaker_status, caretaker_pressure, caretaker_repair.
    odd1.mcpServers.caretaker = { command = "${server}/bin/odd1-caretaker-mcp"; args = [ ]; };

    systemd.services.odd1-caretaker = {
      description = "Odd1 caretaker: watch the machine's health and the foreground program's pressure; report, repair";
      wantedBy = [ "multi-user.target" ];
      after = [ "systemd-tmpfiles-setup.service" ];
      path = tools ++ [ "/run/wrappers" ];  # sudo, for restarting units as the agent user
      environment = {
        PYTHONPATH = "${./.}";
        DISPLAY = ":0";
        ODD1_CARETAKER_STATE = "/var/lib/odd1/caretaker";
        ODD1_CARETAKER_INTERVAL = "60";
        ODD1_CARETAKER_RESTART = lib.concatStringsSep " " config.odd1.caretaker.restartUnits;
      };
      serviceConfig = {
        User = "agent";
        ExecStart = "${python}/bin/python3 -m odd1_caretaker.observer";
        StateDirectory = "odd1/caretaker";
        Restart = "always";
        RestartSec = "10s";
        # The caretaker must never be the one causing pressure.
        MemoryMax = "256M";
        CPUQuota = "10%";
        TasksMax = 32;
      };
    };
  };
}
