"""The nixos-rebuild guard: activating a configuration from the development folder (/srv/odd1) is refused.

Installed as `nixos-rebuild` ahead of the real one: `switch`, `boot` and `test` of a configuration under the
repository (an -I nixos-config=..., a --flake, NIX_PATH, or /etc/nixos/configuration.nix importing it) stop with a
message pointing at odd1-deploy; anything else (building, the deployed release) goes through unchanged. No variable
or flag turns it off: the only way to ship uncommitted work is `odd1-deploy --force`, which a person confirms at a
terminal and which is logged.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ACTIVATING = ("switch", "boot", "test")
REPO = os.environ.get("ODD1_REPO", "/srv/odd1")
REFUSAL = ("nixos-rebuild: refused: {what} is the development folder ({repo}); it may hold uncommitted work, yours or "
           "another session's. Deploy committed work with `odd1-deploy` (a clean release of a commit on main). To ship "
           "the working tree anyway, a person runs `odd1-deploy --force` at a terminal.")


def _under(path: str, repo: str) -> bool:
    real, root = os.path.realpath(path), os.path.realpath(repo)
    return real == root or real.startswith(root + os.sep)


def configs(argv: list[str], env: dict) -> list[str]:
    """Every configuration the arguments and environment name."""
    found = []
    for i, arg in enumerate(argv):
        value = argv[i + 1] if i + 1 < len(argv) else ""
        if arg == "-I" and value.startswith("nixos-config="):
            found.append(value.split("=", 1)[1])
        elif arg.startswith("-Inixos-config="):
            found.append(arg.split("=", 1)[1])
        elif arg == "--flake" and value:
            found.append(value.split("#", 1)[0])
    for part in env.get("NIX_PATH", "").split(":"):
        if part.startswith("nixos-config="):
            found.append(part.split("=", 1)[1])
    return found


def check(argv: list[str], env: dict, etc_config: str = "/etc/nixos/configuration.nix",
          repo: str = REPO) -> str | None:
    """The refusal message, or None when the command may run."""
    if not any(a in ACTIVATING for a in argv):
        return None
    named = configs(argv, env)
    for path in named:
        if _under(path, repo):
            return REFUSAL.format(what=path, repo=repo)
    if not named:
        try:
            text = Path(etc_config).read_text()
        except OSError:
            text = ""
        if f"{os.path.realpath(repo)}/" in text or f"{repo}/" in text:
            return REFUSAL.format(what=f"{etc_config} (it imports from {repo})", repo=repo)
    return None


def main() -> int:
    real = os.environ.get("ODD1_DEPLOY_REAL_REBUILD")
    if not real:
        print("nixos-rebuild guard: the real nixos-rebuild is not known", file=sys.stderr)
        return 1
    refused = check(sys.argv[1:], dict(os.environ))
    if refused:
        print(refused, file=sys.stderr)
        return 3
    os.execv(real, [real, *sys.argv[1:]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
