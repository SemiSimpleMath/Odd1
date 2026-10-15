# any-app-broker: the broker CLI (serve / request) and its test runner. No systemd unit yet: start it per task with
# `any-app-broker serve`; it keeps runtime state under $XDG_RUNTIME_DIR only.
{ pkgs, ... }:

let
  python = pkgs.python3.withPackages (p: [ p.pytest p.tkinter ]);
  tools = with pkgs; [ xorg.xorgserver xorg.xauth xorg.xdpyinfo xdotool imagemagick util-linux systemd ];
  env = ''
    export PATH=${pkgs.lib.makeBinPath tools}:$PATH
  '';
  broker = pkgs.writeShellScriptBin "odd1-any-app-broker" ''
    ${env}
    exec ${python}/bin/python3 ${./.}/any_app_broker/broker.py "$@"
  '';
  test = pkgs.writeShellScriptBin "odd1-any-app-broker-test" ''
    ${env}
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
in {
  environment.systemPackages = [ broker test ];
}
