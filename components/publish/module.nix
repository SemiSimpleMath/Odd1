# publish: odd1-publish pushes the Odd1 repository's main to its GitHub remote with the vault's GITHUB_TOKEN
# (fast-forward only, checked for credentials first). Pushing is the person's: task roles are denied it.
{ pkgs, lib, ... }:

let
  python = pkgs.odd1Python.withPackages (p: [ p.pytest ]);
  env = ''
    export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}
    export PATH=${lib.makeBinPath [ pkgs.git ]}:$PATH  # odd1-secrets comes from the system profile
  '';
  cli = pkgs.writeShellScriptBin "odd1-publish" ''
    ${env}
    exec ${python}/bin/python3 -m odd1_publish.cli "$@"
  '';
  test = pkgs.writeShellScriptBin "odd1-publish-test" ''
    ${env}
    export PYTHONDONTWRITEBYTECODE=1
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
in {
  environment.systemPackages = [ cli test ];
}
