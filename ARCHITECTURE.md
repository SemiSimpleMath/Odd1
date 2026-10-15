# Architecture

Odd1 = NixOS VMs + the components in this repository. This map lists every component; `tools/check.py --map`
prints the generated part from each README's first line. Keep the "connections" notes current by hand.

## Base system (kernel-owned, not in this repository)
- Desktop VM: Xvfb :0 (1280x800), openbox, noVNC on :6080 for people, AT-SPI for accessibility, the app collection
  (Firefox, LibreOffice, GIMP, Inkscape, VSCodium, jEdit, Gnumeric, FeatherPad, ...), xdotool, tesseract.
- Machine VM: sshd, nginx on :80 serving /srv/www, Python and common CLI tools.
- Model access for agents on the VMs: 127.0.0.1:8099 (metered by Orbit's kernel). Claude Code on the Desktop uses
  the user's own sign-in instead.
- Network: package registries only (PyPI, the Nix cache; npm and Anthropic for the Desktop). No general internet.

## Components
(none yet: the existing project work moves here in the first cleanup)

## Connections
(how components call each other: fill in as they appear)
