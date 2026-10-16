# odd1-deploy (CLI), the deploy MCP server and tests. State (releases, the current link, the deploy log, the lock)
# under /var/lib/odd1/deploy; /etc/nixos/configuration.nix imports the host file through <state>/current.
{ config, pkgs, lib, ... }:

let
  python = pkgs.odd1Python.withPackages (p: [ p.pytest p.mcp ]);
  state = "/var/lib/odd1/deploy";
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
    export ODD1_DEPLOY_STATE=''${ODD1_DEPLOY_STATE:-${state}}
    export PATH=${lib.makeBinPath [ pkgs.git pkgs.gnutar pkgs.coreutils ]}:/run/wrappers/bin:/run/current-system/sw/bin:$PATH
  '';
  wrap = name: module: pkgs.writeShellScriptBin name ''
    ${env}
    exec ${python}/bin/python3 -m ${module} "$@"
  '';
  real = "${config.system.build.nixos-rebuild}/bin/nixos-rebuild";
  cli = pkgs.writeShellScriptBin "odd1-deploy" ''
    ${env}
    export ODD1_DEPLOY_REAL_REBUILD=${real}
    exec ${python}/bin/python3 -m odd1_deploy.cli "$@"
  '';
  # Ahead of the real nixos-rebuild (hiPrio): activating the development folder is refused (guard.py).
  guard = lib.hiPrio (pkgs.writeShellScriptBin "nixos-rebuild" ''
    export PYTHONPATH=${./.} ODD1_DEPLOY_REAL_REBUILD=${real}
    exec ${python}/bin/python3 -m odd1_deploy.guard "$@"
  '');
  server = wrap "odd1-deploy-mcp" "odd1_deploy.mcp_server";
  test = pkgs.writeShellScriptBin "odd1-deploy-test" ''
    ${env}
    export PYTHONDONTWRITEBYTECODE=1
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
in {
  environment.systemPackages = [ cli server test guard ];
  # The agent entry point (Odd1 convention): deploy_status, deploy_commit, deploy_rollback.
  odd1.mcpServers.deploy = { command = "${server}/bin/odd1-deploy-mcp"; args = [ ]; };
  systemd.tmpfiles.rules = [ "d ${state} 0755 agent users -" ];
}
