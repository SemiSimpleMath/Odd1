# chat: Claude Code as a chat window (odd1-chat), offered in the chat column's New chat menu as "Chat (preview)".
{ pkgs, ... }:

let
  python = pkgs.python3.withPackages (p: [ p.pygobject3 p.markdown p.pytest ]);
  # The app and its tests; wrapGAppsHook3 gives the commands GTK's and WebKit's typelibs, schemas and modules.
  app = pkgs.stdenv.mkDerivation {
    pname = "odd1-chat";
    version = "0.1";
    src = ./.;
    nativeBuildInputs = [ pkgs.wrapGAppsHook3 pkgs.gobject-introspection ];
    buildInputs = [ pkgs.gtk3 pkgs.webkitgtk_4_1 pkgs.glib-networking ];
    dontBuild = true;
    installPhase = ''
      mkdir -p $out/bin $out/lib
      cp -r odd1_chat tests $out/lib/
      for name in odd1-chat odd1-chat-test; do
        if [ $name = odd1-chat ]; then run="-m odd1_chat.app"; else run="-m pytest -q $out/lib/tests -p no:cacheprovider"; fi
        printf '#!%s\nexport PYTHONPATH=%s''${PYTHONPATH:+:$PYTHONPATH}\nexec %s %s "$@"\n' \
          ${pkgs.bash}/bin/bash $out/lib ${python}/bin/python3 "$run" > $out/bin/$name
        chmod +x $out/bin/$name
      done
    '';
  };
in {
  environment.systemPackages = [ app ];
  # WebKit needs EGL even when it draws on the CPU (no GPU here): Mesa's software renderer.
  hardware.graphics.enable = true;
  # The person talks to it from the chat column (a window of its own, not a terminal).
  odd1.agents.chat = { title = "Chat"; description = "Claude as a chat window (preview)"; command = "odd1-chat"; window = true; };
}
