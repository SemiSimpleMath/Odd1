# odd1-pods and odd1-secrets (CLIs), the pods MCP server and tests. Pods under /var/lib/odd1/pods, secrets' values
# in /var/lib/odd1/secrets (0700, left out of plain backups).
{ pkgs, lib, config, ... }:

let
  python = pkgs.odd1Python.withPackages (p: [ p.pytest p.mcp ]);
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
    export ODD1_PODS_KINDS=''${ODD1_PODS_KINDS:-${./kinds.json}}
  '';
  wrap = name: module: pkgs.writeShellScriptBin name ''
    ${env}
    exec -a ${name} ${python}/bin/python3 -m ${module} "$@"
  '';
  pods = pkgs.writeShellScriptBin "odd1-pods" ''
    ${env}
    exec ${python}/bin/python3 -c 'import sys; from odd1_pods import cli; sys.exit(cli.main(which="pods"))' "$@"
  '';
  secrets = pkgs.writeShellScriptBin "odd1-secrets" ''
    ${env}
    exec ${python}/bin/python3 -c 'import sys; from odd1_pods import cli; sys.exit(cli.main(which="secrets"))' "$@"
  '';
  server = wrap "odd1-pods-mcp" "odd1_pods.mcp_server";
  test = pkgs.writeShellScriptBin "odd1-pods-test" ''
    ${env}
    export PYTHONDONTWRITEBYTECODE=1
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
  # The secret store's tests are the pods tests (secrets are pods); the name task #68 checks.
  secretsTest = pkgs.writeShellScriptBin "odd1-secrets-test" ''exec ${test}/bin/odd1-pods-test "$@"'';
in {
  environment.systemPackages = [ pods secrets server test secretsTest ];
  # The agent entry point (Odd1 convention): pod_put, pod_show, pod_get, pod_find, pod_http, pod_run.
  odd1.mcpServers.pods = { command = "${server}/bin/odd1-pods-mcp"; args = [ ]; };
  systemd.tmpfiles.rules = [ "d /var/lib/odd1/pods 0755 agent users -" "d /var/lib/odd1/secrets 0700 agent users -" ];
}
