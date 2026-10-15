"""Act on one accessible element through the app itself (no pointer, no pixels): works on minimized and covered
windows. Run as a child process, like atspi_dump.

    python -m odd1_screen.atspi_act PID FRAME PATH ROLE NAME (action [NAME] | set-text TEXT)

The element is found by its tree path and must still have the role and name it had in the view, so a changed tree
is an error, never a different element.
"""
import json
import sys

from any_app import describe_accessible as da

# Preferred action names, most specific first; else the element's first action.
PRESS = ("click", "press", "activate", "jump", "toggle", "select")


def resolve(desktop, pid, frame, path, role, name):
    app = None
    for _, candidate in da.children(desktop, limit=200)[0]:
        try:
            if int(candidate.get_process_id()) == pid:
                app = candidate
                break
        except Exception:
            continue
    if app is None:
        raise LookupError(f"no accessible application with pid {pid}")
    node = app.getChildAtIndex(frame)
    for index in ([] if path == "root" else path.split("/")):
        node = node.getChildAtIndex(int(index)) if node is not None else None
    if node is None or da.role_name(node) != role or da.node_name(node) != name:
        raise LookupError("the element moved or changed since the view; take a new view")
    return node


def act(node, op, value=None):
    if op == "action":
        action = node.queryAction()
        names = [action.getName(i) for i in range(action.nActions)]
        if not names:
            raise ValueError("element has no actions")
        wanted = [value] if value else PRESS
        index = next((names.index(n) for n in wanted if n in names), None if value else 0)
        if index is None:
            raise ValueError(f"no action {value!r}; has {names}")
        if not action.doAction(index):
            raise RuntimeError(f"the app refused action {names[index]!r}")
        return {"action": names[index]}
    if op == "set-text":
        if da.role_name(node) == "password text":
            raise PermissionError("will not write password fields this way")
        if not node.queryEditableText().setTextContents(value or ""):
            raise RuntimeError("the app refused the text")
        # Some apps report success and change nothing (Firefox 146 for web form fields): read it back.
        text = node.queryText()
        if text.getText(0, text.characterCount) != (value or ""):
            raise RuntimeError("the app reported success but its text did not change; it does not accept text "
                               "this way (for web pages use the browser component)")
        return {"set_text": len(value or "")}
    raise ValueError(f"unknown operation {op!r}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    pid, frame, path, role, name, op, *rest = argv
    import pyatspi
    try:
        node = resolve(pyatspi.Registry.getDesktop(0), int(pid), int(frame), path, role, name)
        result = act(node, op, rest[0] if rest else None)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
