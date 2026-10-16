# odd1-backup: local backups of Odd1's state (daily) and the person's home folder (hourly), as the agent user, into
# /var/backups/odd1. No model calls.
{ pkgs, lib, ... }:

let
  python = pkgs.odd1Python.withPackages (p: [ p.pytest ]);
  root = "/var/backups/odd1";
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
    export PATH=${lib.makeBinPath [ pkgs.rsync pkgs.coreutils ]}:$PATH
  '';
  cli = pkgs.writeShellScriptBin "odd1-backup" ''
    ${env}
    export ODD1_BACKUP_HOME=''${ODD1_BACKUP_HOME:-/home/agent}
    exec ${python}/bin/python3 -m odd1_backup.cli "$@"
  '';
  test = pkgs.writeShellScriptBin "odd1-backup-test" ''
    ${env}
    export PYTHONDONTWRITEBYTECODE=1
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
  job = what: {
    description = "Odd1 backup: ${what}";
    serviceConfig = { Type = "oneshot"; User = "agent"; ExecStart = "${cli}/bin/odd1-backup run ${what}";
                      Nice = 15; IOSchedulingClass = "idle"; };
  };
in {
  environment.systemPackages = [ cli test ];
  systemd.tmpfiles.rules = [ "d ${root} 0700 agent users -" ];
  systemd.services.odd1-backup = job "state";
  systemd.timers.odd1-backup = { wantedBy = [ "timers.target" ];
                                 timerConfig = { OnCalendar = "03:30"; Persistent = true; }; };
  systemd.services.odd1-backup-home = job "home";
  systemd.timers.odd1-backup-home = { wantedBy = [ "timers.target" ];
                                      timerConfig = { OnCalendar = "hourly"; Persistent = true; }; };
}
