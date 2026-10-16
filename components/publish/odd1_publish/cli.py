"""odd1-publish: push the Odd1 repository's main to its GitHub remote, with the token from the vault.

    odd1-publish                  push main (fast-forward only), after checking it for credentials
    odd1-publish --dry-run        everything but the push
    odd1-publish status           the remote, the token, and what a push would do
    odd1-publish setup URL        set the remote (https://github.com/OWNER/REPO)

The token is the vault's GITHUB_TOKEN (odd1-secrets; --token POD for another): it reaches git only for the push,
through odd1-secrets run, never in a URL, git's config or a file. Never forces; a remote that moved on is refused.
"""
import argparse
import os
import subprocess
import sys

from . import rules

REPO = os.environ.get("ODD1_REPO", "/srv/odd1")
BRANCH = "main"
TOKEN_NAME = "GITHUB_TOKEN"
# git asks this helper for the password; it answers with the variable odd1-secrets puts the token in.
HELPER = '!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f'


class PublishError(Exception):
    pass


def git(*args, check=True, repo=None):
    r = subprocess.run(["git", "-C", repo or REPO, *args], capture_output=True, text=True)
    if check and r.returncode:
        raise PublishError(f"git {args[0]}: {(r.stderr or r.stdout).strip()}")
    return r


def remote_url():
    r = git("remote", "get-url", "origin", check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def token_pod(given=None):
    if given:
        return given
    r = subprocess.run(["odd1-secrets", "list"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == TOKEN_NAME:
            return parts[1]
    raise PublishError(f"no {TOKEN_NAME} in the vault: the person stores it at a terminal with\n"
                       f"  odd1-secrets set {TOKEN_NAME} --allow env:GH_TOKEN --allow host:github.com")


def with_token(pod, *git_args):
    """git with the token for this one command (odd1-secrets scrubs it from the output)."""
    return subprocess.run(["odd1-secrets", "run", "--with", f"GH_TOKEN={pod}", "--",
                           "git", "-C", REPO, "-c", "credential.helper=", "-c", f"credential.helper={HELPER}",
                           *git_args], capture_output=True, text=True)


def check_remote():
    url = remote_url()
    if not url:
        raise PublishError("no remote: odd1-publish setup https://github.com/OWNER/REPO")
    if not rules.remote_ok(url):
        raise PublishError(f"the remote {url!r} is not a plain https://github.com/OWNER/REPO URL")
    return url


def survey(pod):
    """(plan, remote sha, local sha, commits to send) for main."""
    r = with_token(pod, "ls-remote", "origin", f"refs/heads/{BRANCH}")
    if r.returncode:
        raise PublishError(f"cannot reach the remote: {(r.stderr or r.stdout).strip()}")
    remote = r.stdout.split()[0] if r.stdout.strip() else None
    local = git("rev-parse", BRANCH).stdout.strip()
    ancestor = False
    if remote and remote != local:
        f = with_token(pod, "fetch", "--quiet", "origin", BRANCH)
        if f.returncode:
            raise PublishError(f"fetch: {(f.stderr or f.stdout).strip()}")
        ancestor = git("merge-base", "--is-ancestor", remote, local, check=False).returncode == 0
    step = rules.plan(remote, local, ancestor)
    span = BRANCH if remote is None else f"{remote}..{local}"
    count = int(git("rev-list", "--count", span).stdout.strip()) if step in ("create", "fast-forward") else 0
    return step, remote, local, count, span


def publish(dry_run=False, token=None):
    url = check_remote()
    pod = token_pod(token)
    step, remote, local, count, span = survey(pod)
    print(f"{url}  {BRANCH}: {step}" + (f", {count} commit(s)" if count else ""))
    if step == "up-to-date":
        return 0
    if step == "diverged":
        raise PublishError(f"the remote's {BRANCH} ({remote[:12]}) is not in ours: someone pushed elsewhere. "
                           "Nothing was pushed (odd1-publish never forces); a person decides how to merge.")
    found = rules.leaks(git("log", "-p", "--format=", span).stdout)
    if found:
        raise PublishError("these added lines look like credentials; nothing was pushed:\n  " + "\n  ".join(found[:10]))
    if dry_run:
        print("dry run: checks passed, nothing pushed")
        return 0
    r = with_token(pod, "push", "origin", f"{BRANCH}:{BRANCH}")
    if r.returncode:
        raise PublishError(f"push: {(r.stderr or r.stdout).strip()}")
    git("update-ref", f"refs/remotes/origin/{BRANCH}", local)
    print(f"pushed {local[:12]}")
    return 0


def status(token=None):
    url = remote_url()
    print(f"remote: {url or '(none)'}" + ("" if not url or rules.remote_ok(url) else "  (not allowed)"))
    try:
        pod = token_pod(token)
        print(f"token:  {TOKEN_NAME} {pod}")
    except PublishError as e:
        print(f"token:  {e}")
        return 1
    if url and rules.remote_ok(url):
        step, _, _, count, _ = survey(pod)
        print(f"{BRANCH}:   {step}" + (f", {count} commit(s) to send" if count else ""))
    return 0


def setup(url):
    if not rules.remote_ok(url):
        raise PublishError("give a plain https://github.com/OWNER/REPO URL (no token in it)")
    if remote_url() is None:
        git("remote", "add", "origin", url)
    else:
        git("remote", "set-url", "origin", url)
    print(f"remote: {url}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="odd1-publish", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="?", choices=["push", "status", "setup"], default="push")
    p.add_argument("url", nargs="?")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--token", help="the token's pod (default: the vault's GITHUB_TOKEN)")
    a = p.parse_args(argv)
    try:
        if a.command == "setup":
            if not a.url:
                p.error("setup needs the URL")
            return setup(a.url)
        if a.command == "status":
            return status(a.token)
        return publish(a.dry_run, a.token)
    except PublishError as e:
        print(f"odd1-publish: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
