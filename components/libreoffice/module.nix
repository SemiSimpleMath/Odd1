# odd1-calc and the odd1_office Python package, with LibreOffice's UNO bridge on the path.
{ pkgs, ... }:

let
  uno = "${pkgs.libreoffice.unwrapped}/lib/libreoffice/program";
  python = pkgs.python3.withPackages (p: [ p.pytest ]);
  pythonpath = "${./.}:${uno}";
  calc = pkgs.writeShellScriptBin "odd1-calc" ''
    export PYTHONPATH=${pythonpath}''${PYTHONPATH:+:$PYTHONPATH}
    export DISPLAY=''${DISPLAY:-:0}
    exec ${python}/bin/python3 -m odd1_office.cli "$@"
  '';
  test = pkgs.writeShellScriptBin "odd1-calc-test" ''
    export PYTHONPATH=${pythonpath}
    export DISPLAY=''${DISPLAY:-:0}
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
in {
  environment.systemPackages = [ calc test ];
  # For agents' own scripts: PYTHONPATH=$ODD1_OFFICE_PYTHONPATH python3 -c 'from odd1_office.calc import Calc'
  environment.variables.ODD1_OFFICE_PYTHONPATH = pythonpath;
}
