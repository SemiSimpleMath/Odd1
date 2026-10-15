# The Odd1 module: everything Odd1 adds to the base Desktop, from this repository. Applied on the work Desktop by
# /etc/nixos/configuration.nix (the base desktop.nix plus `imports = [ /srv/odd1/nix/odd1.nix ]`), then
#   sudo nixos-rebuild switch
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
in {
  imports = modules;

  # The agent entry point registry (Odd1 convention): each component that lets agents drive a program or use a
  # capability registers its MCP server here; every agent on the machine gets all of them.
  options.odd1.mcpServers = lib.mkOption {
    type = lib.types.attrsOf (lib.types.attrsOf lib.types.anything);
    default = { };
    description = "MCP servers (name -> { command, args, env? }) offered to every agent on Odd1.";
  };

  config = {
  environment.etc."odd1/mcp-servers.json".source = registry;

  networking.hostName = lib.mkForce "odd1";

  # Least friction: programs built for generic Linux run as they are (prebuilt binaries, pip wheels with native
  # code, downloaded tools), not only Nix-built ones.
  programs.nix-ld.enable = true;
  programs.nix-ld.libraries = with pkgs; [ stdenv.cc.cc.lib zlib openssl glib libgcc ];
  environment.localBinInPath = true;
  # A terminal with ordinary copy/paste (Ctrl+Shift+C/V), and a default browser so programs that open links
  # (Claude Code's sign-in, xdg-open) get Firefox on the Odd1 desktop.
  environment.systemPackages = [ pkgs.nodejs pkgs.xdg-utils pkgs.xfce.xfce4-terminal ];
  environment.variables.BROWSER = "firefox";
  xdg.mime.defaultApplications = {
    "text/html" = "firefox.desktop";
    "x-scheme-handler/http" = "firefox.desktop";
    "x-scheme-handler/https" = "firefox.desktop";
  };

  # Claude Code, operated by the person using Odd1 with their own sign-in (it stays on this VM). Installed with npm
  # for the agent user so it keeps itself current; this unit only installs it when it is missing.
  environment.etc."claude-code/CLAUDE.md".source = ../CLAUDE.md;
  systemd.services.odd1-claude-code-install = {
    description = "Install Claude Code for the agent user if missing, and register Odd1's MCP servers with it";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    path = [ pkgs.nodejs pkgs.bash ];
    environment = config.networking.proxy.envVars;
    serviceConfig = { Type = "oneshot"; User = "agent"; RemainAfterExit = true; };
    script = ''
      if [ ! -x "$HOME/.local/bin/claude" ]; then
        npm install -g --prefix "$HOME/.local" @anthropic-ai/claude-code
      fi
      # registry: ${registry}
      ${lib.concatStringsSep "
" (lib.mapAttrsToList (name: server: ''
        "$HOME/.local/bin/claude" mcp remove -s user ${name} >/dev/null 2>&1 || true
        "$HOME/.local/bin/claude" mcp add-json -s user ${name} ${lib.escapeShellArg (builtins.toJSON server)}
      '') servers)}
    '';
  };

  # This VM's identity at Orbit's egress gateway: "odd1" may reach Anthropic and npm (package registries otherwise).
  networking.proxy.default = lib.mkForce "http://odd1:x@10.0.2.100:3128";

  # `sudo nixos-rebuild switch` finds this system's configuration (the base Desktop sets only nixpkgs).
  # toString refers to the pinned nixpkgs without copying it again (a copy per rebuild nests the store name).
  nix.nixPath = lib.mkForce [ "nixpkgs=${toString pkgs.path}" "nixos-config=/etc/nixos/configuration.nix" ];
  nix.registry.nixpkgs.to = lib.mkForce { type = "path"; path = toString pkgs.path; };

  # Runtime state for components lives here, never in the repository.
  systemd.tmpfiles.rules = [ "d /var/lib/odd1 0755 agent users -" ];
  };
}
