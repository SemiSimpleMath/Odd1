# The Odd1 development VM (QEMU): its disk, boot, console, SSH key and network. /etc/nixos/configuration.nix on that
# VM only imports this file. Its network reaches the outside only through an egress proxy at 10.0.2.100:3128
# (package registries and Anthropic); other machines do not have one.
{ modulesPath, ... }:

{
  imports = [ "${modulesPath}/profiles/qemu-guest.nix" ../base.nix ../odd1.nix ];

  boot.loader.grub.device = "/dev/vda";
  boot.growPartition = true;
  boot.kernelParams = [ "console=ttyS0,115200" ];
  fileSystems."/" = { device = "/dev/disk/by-label/nixos"; fsType = "ext4"; autoResize = true; };

  # The host reaches the VM over SSH with this key (it opens the Claude Code windows).
  users.users.agent.openssh.authorizedKeys.keyFiles = [ ./dev-vm-agent.pub ];

  networking.proxy.default = "http://odd1:x@10.0.2.100:3128";

  # The person's screen: the Odd1 desktop, reached with Remote Desktop (port 3389; noVNC stays on 6080).
  odd1.desktop.enable = true;
}
