"""Pure rules of a publish: which remotes are allowed, what may not leave the machine, what a push may do."""
import re

# Shapes of real credentials. A test's obviously fake value (sk-test-..., ghp_xxx...) is not one.
SECRET = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{36,}"
    r"|github_pat_[A-Za-z0-9_]{60,}"
    r"|sk-ant-[A-Za-z0-9_-]{20,}"
    r"|sk-(?!test)[A-Za-z0-9]{32,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|AIza[0-9A-Za-z_-]{35}"
)
REMOTE = re.compile(r"^https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+?(\.git)?$")


def remote_ok(url: str) -> bool:
    """Only a plain GitHub HTTPS URL: no credentials in it, no other host."""
    return bool(REMOTE.match(url or ""))


def leaks(diff: str) -> list[str]:
    """Added lines of a diff that hold something shaped like a credential (shown with the match masked)."""
    found = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            m = SECRET.search(line)
            if m:
                found.append(line[:m.start()] + m.group()[:6] + "…(masked)")
    return found


def plan(remote_sha: str | None, local_sha: str, is_ancestor: bool) -> str:
    """What a push of the branch would do: 'create', 'up-to-date', 'fast-forward' or 'diverged' (refused)."""
    if remote_sha is None:
        return "create"
    if remote_sha == local_sha:
        return "up-to-date"
    return "fast-forward" if is_ancestor else "diverged"
