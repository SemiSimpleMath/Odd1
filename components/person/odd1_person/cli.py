"""odd1-person: the person's settings for people, scripts and agents."""
import argparse
import json
import sys

from . import profile


def main(argv=None):
    parser = argparse.ArgumentParser(prog="odd1-person", description="The person's own settings (About you)")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("show", help="all settings as JSON")
    get = sub.add_parser("get", help="one setting (call_me gives the name to use)")
    get.add_argument("key", choices=profile.KEYS)
    put = sub.add_parser("set", help="change one setting (an empty value clears it)")
    put.add_argument("key", choices=profile.KEYS)
    put.add_argument("value")
    sub.add_parser("context", help="what Claude Code sessions are told about the person")
    sub.add_parser("settings", help="the About you window")
    args = parser.parse_args(argv)

    if args.command == "settings":
        from . import app
        return app.main()
    current = profile.load()
    if args.command == "get":
        print(profile.call_me(current) if args.key == "call_me" else current[args.key])
    elif args.command == "set":
        current = profile.save({args.key: args.value})
        if args.key in ("name", "email"):
            profile.sync_git(current)
        print(json.dumps(current, indent=2, ensure_ascii=False))
    elif args.command == "context":
        print(profile.context(current))
    else:
        print(json.dumps(current, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
