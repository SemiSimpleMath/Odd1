"""What may land: a branch that changes a decision record (wiki/decisions/) lands only when a person approved its
task; the rules are the person's (wiki/decisions/0006). odd1-tasks-land asks `odd1-tasks may-land ID FILE...`."""
from __future__ import annotations

from .store import Store, is_person

RULES = "wiki/decisions/"


def decision_records_need_the_person(task: dict, changed: list[str]) -> str | None:
    """The refusal, or None when the branch may land."""
    touched = [f for f in changed if f.startswith(RULES)]
    if touched and not is_person(task.get("approved_by") or ""):
        return (f"refused: this branch changes {', '.join(touched)}; decision records are the person's, and task "
                f"{task['id']} was approved by {task.get('approved_by') or 'nobody'}, not a person. Propose the "
                f"change as its own task for the person to approve.")
    return None


def main(args: list[str]) -> int:
    if not args:
        print("usage: odd1-tasks may-land ID [FILE...]")
        return 2
    refused = decision_records_need_the_person(Store().show(int(args[0])), args[1:])
    if refused:
        print(refused)
        return 4
    return 0
