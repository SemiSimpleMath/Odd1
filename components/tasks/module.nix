# odd1-tasks (CLI), the tasks MCP server, and one runner per role (odd1-tasks-runner@ROLE). State, role folders
# included, under /var/lib/odd1/tasks; the runners seed the role folders from ./roles where files are missing.
{ config, pkgs, lib, ... }:

let
  python = pkgs.python3.withPackages (p: [ p.pytest p.mcp ]);
  state = "/var/lib/odd1/tasks";
  roles = [ "housekeeper" "update-watcher" ];
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
    export ODD1_TASKS_STATE=''${ODD1_TASKS_STATE:-${state}}
  '';
  wrap = name: module: pkgs.writeShellScriptBin name ''
    ${env}
    exec ${python}/bin/python3 -m ${module} "$@"
  '';
  cli = wrap "odd1-tasks" "odd1_tasks.cli";
  server = wrap "odd1-tasks-mcp" "odd1_tasks.mcp_server";
  test = pkgs.writeShellScriptBin "odd1-tasks-test" ''
    export PYTHONPATH=${./.} PYTHONDONTWRITEBYTECODE=1
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';

  # The Python packages Odd1's components install (every `withPackages (p: [ ... ])` in a module.nix), with the
  # version Nix installs: what the update-watcher compares with PyPI.
  components = ../.;
  moduleTexts = map (n: builtins.readFile (components + "/${n}/module.nix"))
    (builtins.filter (n: builtins.pathExists (components + "/${n}/module.nix"))
      (builtins.attrNames (lib.filterAttrs (_: k: k == "directory") (builtins.readDir components))));
  lists = lib.concatMap (t: builtins.filter builtins.isList (builtins.split "withPackages \\(p: \\[([^]]*)]" t))
    moduleTexts;
  attrs = lib.unique (lib.concatMap (l: map (lib.removePrefix "p.")
    (builtins.filter (s: s != "") (lib.splitString " " (builtins.head l)))) lists);
  # path: the helper reads the PyPI name and version from its .dist-info (Nix's pname is not always PyPI's).
  pyPackages = map (a: { attr = a; name = pkgs.python3Packages.${a}.pname or a;
                         version = pkgs.python3Packages.${a}.version or "unknown";
                         path = "${pkgs.python3Packages.${a}}"; })
    (builtins.filter (a: pkgs.python3Packages ? ${a}) attrs);
in {
  environment.systemPackages = [ cli server test ];
  environment.etc."odd1/tasks/python-packages.json".text = builtins.toJSON pyPackages;
  # The agent entry point (Odd1 convention): tasks_create/list/show/approve/claim/renew/release/log/finish.
  odd1.mcpServers.tasks = { command = "${server}/bin/odd1-tasks-mcp"; args = [ ]; };
  systemd.tmpfiles.rules = [ "d ${state} 0755 agent users -" "d ${state}/roles 0755 agent users -" ];

  # One runner per role: cheap SQL polling, and `claude -p` only when a ready task for the role exists.
  systemd.services."odd1-tasks-runner@" = {
    description = "Odd1 tasks runner for the %i role";
    after = [ "network-online.target" "odd1-claude-code-install.service" ];
    wants = [ "network-online.target" ];
    # Claude Code (~/.local/bin), the helpers the roles' settings allow, and odd1-tasks for done_when checks.
    path = [ python cli pkgs.bash pkgs.coreutils pkgs.findutils pkgs.gnugrep pkgs.gawk pkgs.nodejs
             "/home/agent/.local" ];
    environment = config.networking.proxy.envVars // {
      PYTHONPATH = "${./.}";
      ODD1_TASKS_STATE = state;
      ODD1_TASKS_ROLES_SEED = "${./roles}";
      ODD1_TASKS_PYPKGS = "/etc/odd1/tasks/python-packages.json";
      HOME = "/home/agent";
    };
    serviceConfig = {
      User = "agent";
      ExecStart = "${python}/bin/python3 -m odd1_tasks.runner %i";
      Restart = "always";
      RestartSec = "30s";
      KillMode = "control-group";  # stopping the runner stops its claude too; the lease then frees the task
      CPUWeight = 20;              # background work yields to the person's programs
      IOWeight = 20;
    };
  };
  systemd.targets.multi-user.wants = map (r: "odd1-tasks-runner@${r}.service") roles;
}
