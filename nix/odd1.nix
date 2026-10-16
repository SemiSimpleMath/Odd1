# The Odd1 module: everything Odd1 adds to the base Desktop, from this repository. Applied on the work Desktop by
# a host file (nix/hosts/) that /etc/nixos/configuration.nix imports from the deployed release, put there by
#   odd1-deploy            (an exact commit on main, from a clean export; never the development folder)
# Components join by having a module.nix; it is imported here automatically.
{ config, pkgs, lib, ... }:

let
  components = ../components;
  modules = lib.optionals (builtins.pathExists components)
    (map (name: components + "/${name}/module.nix")
      (builtins.filter (name: builtins.pathExists (components + "/${name}/module.nix"))
        (builtins.attrNames (lib.filterAttrs (_: kind: kind == "directory") (builtins.readDir components)))));
  servers = config.odd1.mcpServers;
  registry = pkgs.writeText "odd1-mcp-servers.json" (builtins.toJSON servers);
  agents = pkgs.writeText "odd1-agents.json" (builtins.toJSON config.odd1.agents);
  # The nixpkgs Odd1 runs on, pinned in the repository (version, url, tarball hash): odd1-deploy and odd1-tasks-check
  # build with it (-I nixpkgs=), so moving to a newer one is a change of nixpkgs.json, checked like any other.
  nixpkgsPin = builtins.fromJSON (builtins.readFile ./nixpkgs.json);
  pinnedNixpkgs = builtins.fetchTarball { inherit (nixpkgsPin) url sha256; };
in {
  imports = modules;

  # The agent entry point registry (Odd1 convention): each component that lets agents drive a program or use a
  # capability registers its MCP server here; every agent on the machine gets all of them.
  options.odd1.mcpServers = lib.mkOption {
    type = lib.types.attrsOf (lib.types.attrsOf lib.types.anything);
    default = { };
    description = "MCP servers (name -> { command, args, env? }) offered to every agent on Odd1.";
  };

  # The agent systems a person can talk to (the desktop's chat column offers each one under "New chat"): a component
  # that runs agents registers how to open a conversation with them. Claude Code itself is the first.
  options.odd1.agents = lib.mkOption {
    type = lib.types.attrsOf (lib.types.submodule {
      options = {
        title = lib.mkOption { type = lib.types.str; description = "Its name in the chat column, e.g. \"Task manager\"."; };
        description = lib.mkOption { type = lib.types.str; default = ""; description = "One line: what talking to it is for."; };
        command = lib.mkOption { type = lib.types.str; description = "Shell command that opens a conversation in a terminal."; };
        window = lib.mkOption { type = lib.types.bool; default = false; description = "The command opens its own window (carrying the role odd1-agent:<name> from $ODD1_AGENT_ROLE) instead of running in a terminal."; };
      };
    });
    default = { };
    description = "Agent systems (name -> { title, description, command }) offered to the person (/etc/odd1/agents.json).";
  };

  config = {
  # Odd1's components run on pkgs.odd1Python: Python 3 with the packages in python-updates.json (name -> version, sdist
  # url, hash), which the tasks updater role writes, at newer versions than nixpkgs has. Only the components' Python
  # sees them, so a pin never rebuilds the rest of the system. Pinned packages skip their own tests (the components'
  # tests are the check).
  nixpkgs.overlays = [ (final: prev: {
    odd1Python = prev.python3.override {
      self = final.odd1Python;
      packageOverrides = pyfinal: pyprev: lib.mapAttrs (name: pin: pyprev.${name}.overridePythonAttrs (old: {
        inherit (pin) version;
        src = prev.fetchurl { inherit (pin) url hash; };
        doCheck = false;
        doInstallCheck = false;
      })) (builtins.fromJSON (builtins.readFile ./python-updates.json));
    };
  }) ];
  environment.etc."odd1/mcp-servers.json".source = registry;
  environment.etc."odd1/agents.json".source = agents;
  # Chats start in auto mode (Claude acts without asking for each step; shift+tab switches modes).
  odd1.agents.claude = { title = "Claude"; description = "A new conversation with Claude Code"; command = "claude --permission-mode auto"; };

  networking.hostName = "odd1";
  time.timeZone = "America/Los_Angeles";  # the person's local time (the top bar's clock, logs, file times)

  # Least friction: programs built for generic Linux run as they are (prebuilt binaries, pip wheels with native
  # code, downloaded tools), not only Nix-built ones.
  programs.nix-ld.enable = true;
  programs.nix-ld.libraries = with pkgs; [ stdenv.cc.cc.lib zlib openssl glib libgcc ];
  environment.localBinInPath = true;
  # A terminal with ordinary copy/paste (Ctrl+Shift+C/V), and a default browser so programs that open links
  # (Claude Code's sign-in, xdg-open) get Firefox on the Odd1 desktop.
  environment.systemPackages = [ pkgs.nodejs pkgs.xdg-utils pkgs.xfce.xfce4-terminal ];
  environment.variables.BROWSER = "firefox";
  # The person (components/person): empty here, the same for every install. Each install sets its person's name and
  # email in its own local settings (/srv/odd1-local/host.nix, never published; odd1-deploy builds it with the
  # release), or the person fills in About you.
  programs.git.enable = true;
  xdg.mime.defaultApplications = {
    "text/html" = "firefox.desktop";
    "x-scheme-handler/http" = "firefox.desktop";
    "x-scheme-handler/https" = "firefox.desktop";
  };

  # Until the Odd1 desktop is switched on: a taskbar, so the person sees every open window (each Claude Code session included) and switches with a click.
  systemd.user.services.odd1-taskbar = lib.mkIf (!config.odd1.desktop.enable) {
    description = "Odd1 taskbar (tint2) on :0";
    wantedBy = [ "default.target" ];
    after = [ "orbit-wm.service" ];
    environment.DISPLAY = ":0";
    serviceConfig = {
      ExecStartPre = "${pkgs.coreutils}/bin/sleep 2";
      ExecStart = "${pkgs.tint2}/bin/tint2 -c ${./tint2rc}";
      Restart = "always";
      RestartSec = 2;
    };
  };

  # Claude Code, operated by the person using Odd1 with their own sign-in (it stays on this VM). Installed with npm
  # for the agent user so it keeps itself current; this unit only installs it when it is missing.
  environment.etc."claude-code/CLAUDE.md".source = ../CLAUDE.md;
  # Claude Code in the person's chats, for every session: it rings the terminal bell when it needs the person (the
  # chat's tab gets a blinking ball instead of taking the focus); it draws with the terminal's palette (the desktop's night
  # palette) and shows no tips; it starts knowing who the person is (their About you, components/person). Managed
  # settings override every session's own, so only what suits every chat.
  environment.etc."claude-code/managed-settings.json".text = builtins.toJSON {
    hooks.SessionStart = [ { hooks = [ { type = "command"; command = "odd1-person context"; } ]; } ];
    preferredNotifChannel = "terminal_bell";
    theme = "dark-ansi";
    spinnerTipsEnabled = false;
  };
  systemd.services.odd1-claude-code-install = {
    description = "Install Claude Code for the agent user if missing, and register Odd1's MCP servers with it";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    path = [ pkgs.nodejs pkgs.bash ];
    environment = config.networking.proxy.envVars;
    serviceConfig = { Type = "oneshot"; User = "agent"; RemainAfterExit = true; };
    script = ''
      # npm's own global folder is the read-only Nix store; with ~/.local as the agent's global prefix, Claude Code's
      # auto-updater (a plain `npm i -g`) installs where Claude Code lives.
      npm config set prefix "$HOME/.local"
      # An update in progress (npm recreates Claude Code's folder) hides ~/.local/bin/claude for a moment: when Claude
      # Code was installed before, wait for claude to come back (up to two minutes) before installing it again.
      if [ -d "$HOME/.local/lib/node_modules/@anthropic-ai" ]; then
        for i in $(seq 120); do [ -x "$HOME/.local/bin/claude" ] && break; sleep 1; done
      fi
      if [ ! -x "$HOME/.local/bin/claude" ]; then
        npm install -g --prefix "$HOME/.local" @anthropic-ai/claude-code
      fi
      # An update can also start while the servers below are registered (each call takes seconds): every call waits
      # for claude to be there, and one that fails is tried again once it is back.
      present() { for i in $(seq 120); do [ -x "$HOME/.local/bin/claude" ] && break; sleep 1; done; }
      claude() { present; "$HOME/.local/bin/claude" "$@" || { sleep 5; present; "$HOME/.local/bin/claude" "$@"; }; }
      # registry: ${registry}
      ${lib.concatStringsSep "
" (lib.mapAttrsToList (name: server: ''
        present; "$HOME/.local/bin/claude" mcp remove -s user ${name} >/dev/null 2>&1 || true
        claude mcp add-json -s user ${name} ${lib.escapeShellArg (builtins.toJSON server)}
      '') servers)}
    '';
  };

  # A build on another nixpkgs than nix/nixpkgs.json pins is not the system the repository describes. A warning, not
  # an error: an odd1-deploy from before the pin builds on the base Desktop's channel (the same tree, another name).
  warnings = lib.optional (toString pkgs.path != pinnedNixpkgs)
    "Odd1 builds with the nixpkgs nix/nixpkgs.json pins (${nixpkgsPin.version}): pass -I nixpkgs=${pinnedNixpkgs} (odd1-deploy and odd1-tasks-check do); this build used ${toString pkgs.path}.";

  # A plain nixos-rebuild finds this system's configuration (the base Desktop sets only nixpkgs) and the pinned
  # nixpkgs; deploy with odd1-deploy. A store path string, so nothing is copied again (a copy nests the store name).
  nix.nixPath = lib.mkForce [ "nixpkgs=${pinnedNixpkgs}" "nixos-config=/etc/nixos/configuration.nix" ];
  nix.registry.nixpkgs.to = lib.mkForce { type = "path"; path = pinnedNixpkgs; };

  # The disk never fills with old builds (it reached 95% on 2026-10-16: 81 generations, ~500 build results): Nix
  # collects garbage daily keeping a week, and during builds whenever free space falls under 2 GB (up to 6 GB freed).
  nix.gc = { automatic = true; dates = "daily"; options = "--delete-older-than 7d"; };
  nix.settings.min-free = 2 * 1024 * 1024 * 1024;
  nix.settings.max-free = 6 * 1024 * 1024 * 1024;
  boot.loader.grub.configurationLimit = 10;

  # Runtime state for components lives here, never in the repository.
  systemd.tmpfiles.rules = [ "d /var/lib/odd1 0755 agent users -" ];
  };
}
