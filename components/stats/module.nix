# Stats: a sample every 15 minutes (odd1-stats.timer), the odd1-stats CLI, its MCP server and tests.
# State under /var/lib/odd1/stats; it only reads tasks, Claude Code transcripts, caretaker and deploy.
{ config, pkgs, lib, ... }:

let
  python = pkgs.odd1Python.withPackages (p: [ p.pytest p.mcp ]);
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
  '';
  wrap = name: module: pkgs.writeShellScriptBin name ''
    ${env}
    exec ${python}/bin/python3 -m ${module} "$@"
  '';
  cli = wrap "odd1-stats" "odd1_stats.cli";
  server = wrap "odd1-stats-mcp" "odd1_stats.mcp_server";
  test = pkgs.writeShellScriptBin "odd1-stats-test" ''
    ${env}
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
in {
  environment.systemPackages = [ cli server test ];
  # The agent entry point (Odd1 convention): stats_summary, stats_trend.
  odd1.mcpServers.stats = { command = "${server}/bin/odd1-stats-mcp"; args = [ ]; };

  systemd.services.odd1-stats = {
    description = "Odd1 stats: one sample of agent and system stats";
    serviceConfig = {
      Type = "oneshot";
      User = "agent";  # reads the agent's Claude Code transcripts and the tasks store
      Environment = [ "HOME=/home/agent" "ODD1_STATS_STATE=/var/lib/odd1/stats" ];
      ExecStart = "${cli}/bin/odd1-stats sample";
      StateDirectory = "odd1/stats";
      Nice = 15;
      IOSchedulingClass = "idle";
      MemoryMax = "256M";
    };
  };
  systemd.timers.odd1-stats = {
    description = "Odd1 stats: a sample every 15 minutes";
    wantedBy = [ "timers.target" ];
    timerConfig = { OnActiveSec = "1min"; OnUnitActiveSec = "15min"; Persistent = false; };
  };
}
