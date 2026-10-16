"""odd1-desktop: the desktop shell and its actions.

    odd1-desktop shell [--display :0]   run the shell (chat column, wallpaper, keeping apps out of the column)
    odd1-desktop new-chat [DIR]         open a new chat (Claude Code, or $ODD1_CHAT_COMMAND) in the column
    odd1-desktop focus-chat             give the keyboard to the chat on top of the column
    odd1-desktop chat [--title T] [--role R] [--cwd DIR] [--command CMD] [--prompt TEXT] [--undocked]
                                        a chat window: a terminal under a bar (dock/undock, this chat's settings)
    odd1-desktop chansrv                hand each new Remote Desktop connection to xrdp-chansrv (clipboard, drives)
"""
import argparse
import os
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="odd1-desktop", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["shell", "new-chat", "focus-chat", "chat", "chansrv"])
    parser.add_argument("dir", nargs="?", help="new-chat: working directory")
    parser.add_argument("--display", default=os.environ.get("DISPLAY") or ":0")
    parser.add_argument("--title", default="Claude Code", help="chat: the window's title")
    parser.add_argument("--role", default="odd1-agent:claude", help="chat: the role the column docks by")
    parser.add_argument("--cwd", help="chat: working directory")
    parser.add_argument("--command", dest="run", help="chat: what it runs (default: claude)")
    parser.add_argument("--prompt", help="chat: the first message (a Claude chat's opening prompt)")
    parser.add_argument("--undocked", action="store_true", help="chat: start as a window of its own")
    args = parser.parse_args(argv)
    if args.command == "chat":
        from . import chatwindow
        return chatwindow.main(args)
    if args.command == "chansrv":
        from . import chansrv
        sockets, display = f"/run/xrdp/{os.getuid()}", args.display.lstrip(":").split(".")[0]
        chansrv.run(f"{sockets}/odd1_chansrv_socket_{display}", f"{sockets}/xrdp_chansrv_socket_{display}")
        return 0
    from . import shell
    if args.command == "shell":
        shell.Shell(args.display).serve()
    elif args.command == "new-chat":
        shell.open_chat(args.dir)
    elif not shell.focus_chat(args.display):
        print("odd1-desktop: no chat open", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
