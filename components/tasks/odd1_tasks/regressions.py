"""No landing makes Odd1 worse (rule 4): a branch's build runs every component's installed tests, not only the
changed ones. The changed (and named) components' tests must pass; any other component's failure is a regression
unless the running system's own test fails the same way (already broken before this branch, reported, not blamed).

    odd1-tasks regressions BRANCH_BIN MAIN_BIN [STRICT_COMPONENT...]
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TIMEOUT = float(os.environ.get("ODD1_TASKS_TEST_TIMEOUT", 900))
# Tests that open windows do it on the agent desktop, never on the person's screen (:0): focus is the person's.
TEST_ENV = dict(os.environ, DISPLAY=os.environ.get("ODD1_TEST_DISPLAY", ":1"))


def run_test(binary: Path) -> int:
    for _ in range(2):  # a failure is retried once: a flaky test is not a regression
        try:
            code = subprocess.run([str(binary), "-q"], capture_output=True, timeout=TIMEOUT, env=TEST_ENV).returncode
        except subprocess.TimeoutExpired:
            code = 124
        if code in (0, 5):  # 5: no tests collected
            return 0
    return code


def tests(bin_dir: Path) -> dict[str, Path]:
    return {p.name[len("odd1-"):-len("-test")]: p for p in sorted(Path(bin_dir).glob("odd1-*-test"))}


def judge(branch: dict[str, int], main: dict[str, int | None], strict: set[str]) -> dict:
    """branch: component -> exit code on the branch; main: component -> exit code on the running system (None: no
    such test there). Returns regressions (block), already_failing (reported) and passed."""
    out = {"regressions": [], "already_failing": [], "passed": []}
    for c, code in sorted(branch.items()):
        if code == 0:
            out["passed"].append(c)
        elif c in strict or not main.get(c):
            out["regressions"].append(c)
        else:
            out["already_failing"].append(c)
    return out


def main(args: list[str]) -> int:
    if len(args) < 2:
        print(__doc__)
        return 2
    branch_bin, main_bin, strict = Path(args[0]), Path(args[1]), set(args[2:])
    on_main = tests(main_bin)
    branch, main_results = {}, {}
    for c, binary in tests(branch_bin).items():
        print(f"== odd1-{c}-test", flush=True)
        branch[c] = run_test(binary)
        if branch[c] and c not in strict:
            main_results[c] = run_test(on_main[c]) if c in on_main else None
    verdict = judge(branch, main_results, strict)
    for c in verdict["already_failing"]:
        print(f"!! odd1-{c}-test fails here and on the running system too: not this branch's doing (file friction)")
    for c in verdict["regressions"]:
        print(f"XX odd1-{c}-test fails on this branch" + ("" if c in strict else " but passes on the running system"))
    print(f"{len(verdict['passed'])} components' tests pass, {len(verdict['regressions'])} regressions")
    return 1 if verdict["regressions"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
