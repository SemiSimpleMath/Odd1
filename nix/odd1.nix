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
in {
  imports = modules;

  networking.hostName = lib.mkForce "odd1";

  # Claude Code, operated by the person using Odd1 with their own sign-in (it stays on this VM).
  nixpkgs.config.allowUnfreePredicate = pkg: builtins.elem (lib.getName pkg) [ "claude-code" ];
  environment.systemPackages = [ pkgs.claude-code pkgs.nodejs ];
  environment.etc."claude-code/CLAUDE.md".source = ../CLAUDE.md;

  # This VM's identity at Orbit's egress gateway: "odd1" may reach Anthropic and npm (package registries otherwise).
  networking.proxy.default = lib.mkForce "http://odd1:x@10.0.2.100:3128";

  # Runtime state for components lives here, never in the repository.
  systemd.tmpfiles.rules = [ "d /var/lib/odd1 0755 agent users -" ];
}
