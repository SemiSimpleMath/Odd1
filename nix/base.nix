# The Odd1 base system: the person's and agents' user, the screen, the apps, and the tools to see and drive them.
# Machine-independent; a host profile (nix/hosts/) adds disks, boot and network, and imports this and odd1.nix.
{ config, pkgs, lib, ... }:

let
  desktop = config.odd1.desktop.enable;
  python = pkgs.python3.withPackages (p: [ p.pyatspi p.pygobject3 p.pillow p.tkinter ]);
  # A display unit counts as started only once :0 answers, so units ordered after it never race the X server.
  waitForDisplay = pkgs.writeShellScript "odd1-display-wait" ''
    for _ in $(seq 100); do ${pkgs.xorg.xdpyinfo}/bin/xdpyinfo -display :0 >/dev/null 2>&1 && exit 0; sleep 0.1; done
    exit 1
  '';
in {
  system.stateVersion = "25.05";

  # One user, `agent`: the person's desktop session and the agents' tools run as it. Agents get sudo (an Odd1
  # machine is theirs to operate).
  users.users.agent = {
    isNormalUser = true;
    uid = 1000;
    extraGroups = [ "wheel" "systemd-journal" ];
    linger = true;  # the graphical session runs without anyone logged in
  };
  security.sudo.wheelNeedsPassword = false;
  services.openssh = {
    enable = true;
    settings = { PasswordAuthentication = false; PermitRootLogin = "no"; };
  };

  # The screen: X display :0 and the accessibility bus, in the agent's user session; anything run over SSH as agent
  # sees the same display. `odd1.desktop.enable` (components/desktop) switches it from the fixed-size Xvfb with
  # openbox and noVNC (units named after the VM this was ported from) to Xvnc with the Odd1 desktop and Remote
  # Desktop.
  services.gnome.at-spi2-core.enable = true;
  fonts.packages = with pkgs; [ dejavu_fonts liberation_ttf noto-fonts ];
  networking.firewall.allowedTCPPorts = [ 6080 ];
  systemd.user.services = lib.mkMerge [
    (lib.mkIf (!desktop) {
      # Until the switch: a fixed-size Xvfb, openbox, and x11vnc for noVNC.
      orbit-display = {
        description = "Headless X display :0";
        wantedBy = [ "default.target" ];
        serviceConfig = {
          ExecStart = "${pkgs.xorg.xorgserver}/bin/Xvfb :0 -screen 0 1280x800x24 -nolisten tcp";
          ExecStartPost = "${waitForDisplay}";
          Restart = "always";
        };
      };
      orbit-wm = {
        description = "Window manager on :0";
        wantedBy = [ "default.target" ];
        after = [ "orbit-display.service" ];
        requires = [ "orbit-display.service" ];
        environment.DISPLAY = ":0";
        serviceConfig = {
          ExecStartPre = "${pkgs.coreutils}/bin/sleep 1";
          ExecStart = "${pkgs.openbox}/bin/openbox";
          Restart = "always";
        };
      };
      # The person's view: VNC on the display, served to a web browser by noVNC on port 6080.
      orbit-vnc = {
        description = "VNC server on display :0";
        wantedBy = [ "default.target" ];
        after = [ "orbit-display.service" ];
        requires = [ "orbit-display.service" ];
        serviceConfig = {
          ExecStartPre = "${pkgs.coreutils}/bin/sleep 2";
          ExecStart = "${pkgs.x11vnc}/bin/x11vnc -display :0 -forever -shared -localhost -nopw -rfbport 5900 -quiet";
          Restart = "always";
        };
      };
      orbit-novnc = {
        description = "noVNC web viewer on port 6080";
        wantedBy = [ "default.target" ];
        after = [ "orbit-vnc.service" ];
        serviceConfig = {
          ExecStartPre = "${pkgs.coreutils}/bin/sleep 3";
          ExecStart = "${pkgs.python3Packages.websockify}/bin/websockify --web ${pkgs.novnc}/share/webapps/novnc 6080 localhost:5900";
          Restart = "always";
        };
      };
    })
    (lib.mkIf desktop {
      # The screen is Xvnc: an X server with VNC built in that resizes to whatever its viewer asks for (Remote
      # Desktop's window, noVNC's). VNC itself only listens on localhost; people come in through xrdp or noVNC.
      odd1-display = {
        description = "The person's screen: X display :0 (Xvnc, resizes with the viewer)";
        wantedBy = [ "default.target" ];
        serviceConfig = {
          ExecStart = "${pkgs.tigervnc}/bin/Xvnc :0 -geometry 1280x800 -depth 24 -rfbport 5900 -localhost "
            + "-SecurityTypes None -AcceptSetDesktopSize -AlwaysShared -nolisten tcp -desktop Odd1";
          ExecStartPost = "${waitForDisplay}";
          Restart = "always";
        };
      };
      odd1-novnc = {
        description = "noVNC web viewer on port 6080 (fallback to Remote Desktop)";
        wantedBy = [ "default.target" ];
        after = [ "odd1-display.service" ];
        serviceConfig = {
          ExecStartPre = "${pkgs.coreutils}/bin/sleep 2";
          ExecStart = "${pkgs.python3Packages.websockify}/bin/websockify --web ${pkgs.novnc}/share/webapps/novnc 6080 localhost:5900";
          Restart = "always";
        };
      };
    })
  ];
  # Remote Desktop (RDP, port 3389) straight onto the person's screen: xrdp connects every client to :0's VNC, and
  # passes the client's window size on, so the screen always matches the viewer pixel for pixel.
  services.xrdp = lib.mkIf desktop {
    enable = true;
    openFirewall = true;
    extraConfDirCommands = ''
      awk '/^\[Xorg\]/{exit} {print}' $out/xrdp.ini > $out/xrdp.ini.odd1
      mv $out/xrdp.ini.odd1 $out/xrdp.ini
      sed -i 's/^autorun=.*/autorun=Odd1/' $out/xrdp.ini
      printf '%s\n' '[Odd1]' 'name=Odd1' 'lib=libvnc.so' 'ip=127.0.0.1' 'port=5900' 'username=na' 'password=' \
        'chansrvport=/run/xrdp/1000/xrdp_chansrv_socket_0' >> $out/xrdp.ini
      # chansrv (the clipboard bridge, components/desktop) mounts no Windows drives.
      sed -i 's/^#EnableFuseMount=false/EnableFuseMount=false/' $out/sesman.ini
    '';
  };
  # The clipboard with images: xrdp hands the clipboard channel to chansrv, which runs as agent on :0 (user unit
  # odd1-desktop-clipboard) and listens in /run/xrdp/<uid>/; setgid xrdp, so xrdp may connect to its socket.
  systemd.tmpfiles.rules = lib.mkIf desktop [ "d /run/xrdp/1000 2770 agent xrdp -" ];

  environment.sessionVariables = {
    DISPLAY = ":0";
    XDG_RUNTIME_DIR = "/run/user/1000";
    DBUS_SESSION_BUS_ADDRESS = "unix:path=/run/user/1000/bus";
    NO_AT_BRIDGE = "0";
    GTK_MODULES = "gail:atk-bridge";
    # GObject introspection data, so Python's pyatspi (and gi) can load the accessibility API.
    GI_TYPELIB_PATH = lib.makeSearchPath "lib/girepository-1.0" (with pkgs; [ at-spi2-core glib.out gtk3 pango.out gdk-pixbuf harfbuzz ]);
  };

  # Programs to work with, and tools to see and drive the screen.
  environment.systemPackages = with pkgs; [
    firefox libreoffice gnome-calculator xfce.mousepad xterm
    # The app zoo: one or more programs per way GUIs are built.
    featherpad gimp inkscape vscodium jedit gnumeric nano
    xdotool wmctrl xorg.xwininfo xorg.xprop xorg.xdpyinfo xorg.xev imagemagick scrot tesseract
    at-spi2-core python
    git curl jq vim htop file tree ripgrep
  ];

  networking.proxy.noProxy = "127.0.0.1,localhost";
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  nix.nixPath = [ "nixpkgs=${pkgs.path}" ];
  nix.registry.nixpkgs.to = { type = "path"; path = pkgs.path; };
  nix.settings.flake-registry = "";

  services.journald.extraConfig = "SystemMaxUse=200M";
  documentation.enable = false;
}
