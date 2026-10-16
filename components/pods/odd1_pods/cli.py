"""odd1-pods and odd1-secrets: pods (content by reference) and secrets (pods agents handle but never read).

    odd1-pods put KIND [--text T | --file F] [--one-liner S] [--tag T]...   -> pod:<kind>:<id>
    odd1-pods get URI [--projection P]       odd1-pods show URI        odd1-pods find [--kind K] [--tag T] [--text S]
    odd1-pods audit [URI]

    odd1-secrets list                                    names, references, where each may go (never values)
    odd1-secrets set NAME [--kind secret.token] [--allow DEST]...   a person types the value (hidden)
    odd1-secrets import FILE.env [--allow VAR=DEST]...   a person stores every variable of a .env file
    odd1-secrets allow URI DEST...                        a person says where a secret may go
    odd1-secrets run --with VAR=URI... -- COMMAND...      run with secrets in the environment (DEST env:VAR)
    odd1-secrets http METHOD URL [-H 'Name: value']... [--data BODY]   {{pod:...}} placeholders (DEST host:NAME)
DEST: host:api.example.com, env:OPENAI_API_KEY, field:card-number; a trailing * matches a prefix.
Storing and allowing need a person at a terminal; values never print.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from . import courier
from .store import PodError, Pods


def _person_at_terminal() -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise PodError("a person does this at a terminal (agents never store or route secrets)")


def parse_env(text: str) -> dict[str, str]:
    """KEY=value lines of a .env file (export, quotes and comments allowed)."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key.strip()] = value
    return out


def pods_main(argv) -> int:
    p = argparse.ArgumentParser(prog="odd1-pods", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    put = sub.add_parser("put")
    put.add_argument("kind")
    put.add_argument("--text", default="")
    put.add_argument("--file")
    put.add_argument("--one-liner", default="")
    put.add_argument("--tag", action="append", default=[])
    put.add_argument("--source", action="append", default=[])
    get = sub.add_parser("get")
    get.add_argument("uri")
    get.add_argument("--projection", default="body")
    sub.add_parser("show").add_argument("uri")
    find = sub.add_parser("find")
    find.add_argument("--kind")
    find.add_argument("--tag")
    find.add_argument("--text")
    sub.add_parser("audit").add_argument("uri", nargs="?")
    a = p.parse_args(argv)
    pods = Pods()
    if a.cmd == "put":
        print(pods.put(a.kind, a.text, one_liner=a.one_liner, tags=a.tag, file=a.file, sources=a.source))
    elif a.cmd == "get":
        print(pods.get(a.uri, a.projection))
    elif a.cmd == "show":
        print(json.dumps(pods.header(a.uri), indent=1))
    elif a.cmd == "find":
        for h in pods.find(a.kind, a.tag, a.text):
            print(f"{h['uri']}  {h['one_liner']}")
    else:
        for e in pods.audit(a.uri):
            print(f"{e['id']} {e['actor']} {e['op']} {e['uri']} {e['projection'] or ''} "
                  f"{'ok' if e['allowed'] else 'REFUSED'} {e['detail']}")
    return 0


def secrets_main(argv) -> int:
    p = argparse.ArgumentParser(prog="odd1-secrets", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("set")
    s.add_argument("name")
    s.add_argument("--kind", default="secret.token")
    s.add_argument("--allow", action="append", default=[])
    i = sub.add_parser("import")
    i.add_argument("file", type=Path)
    i.add_argument("--allow", action="append", default=[], help="VAR=DEST")
    al = sub.add_parser("allow")
    al.add_argument("uri")
    al.add_argument("dest", nargs="+")
    r = sub.add_parser("run")
    r.add_argument("--with", dest="with_", action="append", default=[], help="VAR=URI")
    r.add_argument("command", nargs=argparse.REMAINDER)
    h = sub.add_parser("http")
    h.add_argument("method")
    h.add_argument("url")
    h.add_argument("-H", dest="headers", action="append", default=[])
    h.add_argument("--data", default="")
    a = p.parse_args(argv)
    pods = Pods()
    if a.cmd == "list":
        for k in sorted(pods.kinds):
            if pods.kinds[k].get("secret"):
                for x in pods.find(kind=k, limit=1000):
                    tail = pods.db.execute("SELECT value FROM projections WHERE uri = ? AND name = 'last4'",
                                           (x["uri"],)).fetchone()
                    print(f"{x['one_liner']:24} {x['uri']}  …{tail[0] if tail else '????'}  "
                          f"may go to: {', '.join(x['allow']) or 'nowhere yet'}")
    elif a.cmd == "set":
        _person_at_terminal()
        value = getpass.getpass(f"Value of {a.name} (hidden): ")
        print(pods.put_secret(a.kind, value, name=a.name, allow=a.allow))
    elif a.cmd == "import":
        _person_at_terminal()
        allow = {}
        for item in a.allow:
            var, dest = item.split("=", 1)
            allow.setdefault(var, []).append(dest)
        for var, value in parse_env(a.file.read_text()).items():
            if value:
                print(f"{var:24} {pods.put_secret('secret.token', value, name=var, allow=allow.get(var, []))}")
        print(f"Stored. {a.file} is still there: delete it yourself once you have checked the list.")
    elif a.cmd == "allow":
        _person_at_terminal()
        print(json.dumps(pods.allow(a.uri, a.dest), indent=1))
    elif a.cmd == "run":
        env = dict(item.split("=", 1) for item in a.with_)
        command = a.command[1:] if a.command[:1] == ["--"] else a.command
        out = courier.run(pods, command, env)
        sys.stdout.write(out["stdout"])
        sys.stderr.write(out["stderr"])
        return out["exit"]
    else:
        headers = dict(x.split(":", 1) for x in a.headers)
        print(json.dumps(courier.http(pods, a.method, a.url, {k.strip(): v.strip() for k, v in headers.items()},
                                      a.data), indent=1))
    return 0


def main(argv=None, which: str | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    which = which or ("secrets" if Path(sys.argv[0]).name == "odd1-secrets" else "pods")
    try:
        return (secrets_main if which == "secrets" else pods_main)(argv)
    except PodError as e:
        print(f"odd1-{which}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
