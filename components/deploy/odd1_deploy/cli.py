"""odd1-deploy: build and activate one approved commit of /srv/odd1 from its own clean release folder.

    odd1-deploy [COMMIT] [--boot | --build] [--no-wait] [--reason TEXT]
                                   deploy COMMIT (default main; it must be on main); one deploy at a time
    odd1-deploy rollback           deploy again the commit that was current before
    odd1-deploy --force [--reason TEXT]
                                   a person ships /srv/odd1's working tree as it is (asks at the terminal; logged)
    odd1-deploy status [--json]    the deployed commit, how far main is ahead, recent deploys
    odd1-deploy host               the host's configuration file inside the repository (e.g. nix/hosts/dev-vm.nix)
    odd1-deploy config-for DIR     a configuration that builds checkout DIR with this install's local settings
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from .deploy import Deployer, DeployError


def _line(d: dict) -> str:
    when = datetime.fromtimestamp(d["at"]).strftime("%Y-%m-%d %H:%M")
    extra = f" ({d['reason']})" if d.get("reason") else ""
    return f"{when}  {d['commit'][:12]}  {d['action']:6} {'ok' if d['ok'] else 'FAILED':6} {d['actor']}{extra}"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    d = Deployer()
    try:
        if argv[:1] == ["status"]:
            s = d.status()
            if "--json" in argv:
                print(json.dumps(s, indent=2))
                return 0
            now = s["current"]
            subject = d._git("log", "-1", "--format=%s", now) if now else ""
            print(f"current {now[:12] if now else 'none'} {subject}".rstrip())
            if s["local"] or s["local_head"]:
                print(f"local   {(s['local'] or 'none')[:12]} ({d.local}" + (", newer commits not deployed)"
                      if s["local_head"] and s["local_head"] != s["local"] else ")"))
            print(f"main    {s['main'][:12]}" + ("" if s["main_ahead_by"] is None else
                                                 f" ({s['main_ahead_by']} commits not deployed)"))
            if s["forced"]:
                f = s["forced"]
                print(f"FORCED  the system runs {d.repo}'s working tree ({len(f['dirty'])} uncommitted changes), "
                      f"by {f['actor']} on {f['terminal']}: {f['reason']}")
            print(f"configuration {'imports the deployed release' if s['configuration_uses_release'] else 'does NOT import the deployed release'}"
                  f" ({d.nixos_config})")
            for h in s["history"]:
                print("  " + _line(h))
            return 0
        if argv[:1] == ["config-for"] and len(argv) == 2:
            print(d.config_for(argv[1]))
            return 0
        if argv[:1] == ["host"]:
            print(d.host())
            return 0
        if argv[:1] == ["rollback"]:
            print(_line(d.rollback()))
            return 0
        p = argparse.ArgumentParser(prog="odd1-deploy", description=__doc__,
                                    formatter_class=argparse.RawDescriptionHelpFormatter)
        p.add_argument("commit", nargs="?", default="main")
        g = p.add_mutually_exclusive_group()
        g.add_argument("--boot", action="store_const", const="boot", dest="action")
        g.add_argument("--build", action="store_const", const="build", dest="action")
        p.add_argument("--no-wait", action="store_true", help="fail at once if another deploy is running")
        p.add_argument("--reason", default="")
        p.add_argument("--force", action="store_true", help="a person ships the working tree (asks at the terminal)")
        a = p.parse_args(argv)
        if a.force:
            print(_line(d.force(a.reason)))
            return 0
        print(_line(d.deploy(a.commit, a.action or "switch", wait=not a.no_wait, reason=a.reason)))
        return 0
    except DeployError as e:
        print(f"odd1-deploy: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
