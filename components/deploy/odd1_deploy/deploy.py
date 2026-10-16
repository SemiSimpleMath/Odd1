"""Deploy one approved commit of the Odd1 repository, and nothing else.

A deploy resolves a commit on main (approved work: main only moves by commits that passed their checks), exports
exactly that commit with `git archive` into its own read-only release folder (<state>/releases/<sha>), and builds and
switches the system from there. It only reads git objects: the shared checkout's working tree, index, stash,
branches and the agents' task worktrees are never touched, so nobody's uncommitted or pending work ships or changes.
Once the switch succeeded, <state>/current points at the release and /etc/nixos/configuration.nix imports the host
file through it, so a plain `nixos-rebuild switch` rebuilds the deployed release, never the development folder.
One deploy at a time (<state>/lock); each is recorded in <state>/deployments.jsonl; rollback deploys the commit
that was current before.
"""
from __future__ import annotations

import fcntl
import json
import os
import shlex
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

KEEP = 5  # releases kept on disk (the current one and the latest before it), for rollback without a fresh export
BRANCH = "main"


class DeployError(Exception):
    pass


class Deployer:
    def __init__(self, state=None, repo=None, nixos_config=None, rebuild=None, sudo=None, host=None):
        env = os.environ
        self.state = Path(state or env.get("ODD1_DEPLOY_STATE", "/var/lib/odd1/deploy"))
        self.repo = Path(repo or env.get("ODD1_REPO", "/srv/odd1"))
        self.nixos_config = Path(nixos_config or env.get("ODD1_DEPLOY_NIXOS_CONFIG", "/etc/nixos/configuration.nix"))
        # The command that builds and activates a configuration: `<rebuild> ACTION -I nixos-config=FILE`.
        self.rebuild = shlex.split(rebuild if rebuild is not None else env.get("ODD1_DEPLOY_REBUILD",
                                                                                "sudo -n nixos-rebuild"))
        # The prefix that may write /etc/nixos (empty when the file is the caller's own, as in tests).
        self.sudo = shlex.split(sudo if sudo is not None else env.get("ODD1_DEPLOY_SUDO", "sudo -n"))
        self._host = host or env.get("ODD1_DEPLOY_HOST")

    # -- git, read-only -----------------------------------------------------------------------------------------------
    def _git(self, *args) -> str:
        p = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True)
        if p.returncode:
            raise DeployError(f"git {' '.join(args)}: {p.stderr.strip()}")
        return p.stdout.strip()

    def resolve(self, commit: str = BRANCH) -> str:
        """The full sha of COMMIT, which must be on main (approved, committed work)."""
        sha = self._git("rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
        p = subprocess.run(["git", "-C", str(self.repo), "merge-base", "--is-ancestor", sha, BRANCH])
        if p.returncode:
            raise DeployError(f"{commit} ({sha[:12]}) is not on {BRANCH}: only approved, landed commits deploy")
        return sha

    # -- the host file ------------------------------------------------------------------------------------------------
    def host(self) -> str:
        """The host's configuration inside the repository (e.g. nix/hosts/dev-vm.nix): $ODD1_DEPLOY_HOST, else
        <state>/host, else read once from /etc/nixos/configuration.nix (which imported it from /srv/odd1)."""
        if self._host:
            return self._host
        saved = self.state / "host"
        if saved.exists():
            return saved.read_text().strip()
        text = self.nixos_config.read_text() if self.nixos_config.exists() else ""
        for prefix in (f"{self.repo}/", f"{self.state}/current/"):
            start = text.find(prefix)
            if start >= 0:
                end = start + len(prefix)
                while end < len(text) and text[end] not in " ;]\n\"":
                    end += 1
                found = text[start + len(prefix):end]
                self.state.mkdir(parents=True, exist_ok=True)
                saved.write_text(found + "\n")
                return found
        raise DeployError(f"no host file: {self.nixos_config} imports none from {self.repo}; set ODD1_DEPLOY_HOST")

    # -- releases -----------------------------------------------------------------------------------------------------
    def release(self, sha: str) -> Path:
        """<state>/releases/<sha>: exactly the commit's tree (git archive), read-only. Made once, then reused."""
        where = self.state / "releases" / sha
        if (where / ".odd1-release").exists():
            return where
        where.parent.mkdir(parents=True, exist_ok=True)
        tmp = where.parent / f".tmp-{sha}-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        archive = subprocess.Popen(["git", "-C", str(self.repo), "archive", "--format=tar", sha],
                                   stdout=subprocess.PIPE)
        untar = subprocess.run(["tar", "-x", "-C", str(tmp)], stdin=archive.stdout, capture_output=True)
        archive.stdout.close()
        if archive.wait() or untar.returncode:
            shutil.rmtree(tmp, ignore_errors=True)
            raise DeployError(f"could not export {sha[:12]}: {untar.stderr.decode().strip()}")
        (tmp / ".odd1-release").write_text(sha + "\n")
        _read_only(tmp)
        if where.exists():  # a half-made release from an interrupted run
            _remove(where)
        tmp.rename(where)
        return where

    def current(self) -> str | None:
        link = self.state / "current"
        try:
            return (link.resolve() / ".odd1-release").read_text().strip()
        except OSError:
            return None

    def _point(self, sha: str) -> None:
        """current -> releases/<sha> (atomically), and the system's configuration imports the host file through it."""
        link, tmp = self.state / "current", self.state / ".current.tmp"
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(Path("releases") / sha)
        os.replace(tmp, link)
        want = (f"# Written by odd1-deploy: this machine runs the deployed release of the Odd1 repository (one commit,\n"
                f"# {link} -> releases/<sha>), never the development folder. Deploy with: odd1-deploy\n"
                f"{{ imports = [ {link}/{self.host()} ]; }}\n")
        if self.nixos_config.exists() and self.nixos_config.read_text() == want:
            return
        staged = self.state / "configuration.nix"
        staged.write_text(want)
        p = subprocess.run([*self.sudo, "install", "-m", "644", str(staged), str(self.nixos_config)],
                           capture_output=True, text=True)
        if p.returncode:
            raise DeployError(f"could not write {self.nixos_config}: {p.stderr.strip()}")

    def _prune(self) -> None:
        keep = {self.current()} | {d["commit"] for d in self.history()[-KEEP:] if d["ok"]}
        for where in (self.state / "releases").glob("*"):
            if where.name not in keep:
                _remove(where)

    # -- deploying ----------------------------------------------------------------------------------------------------
    @contextmanager
    def _lock(self, wait: bool = True):
        self.state.mkdir(parents=True, exist_ok=True)
        with open(self.state / "lock", "w") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
            except BlockingIOError:
                raise DeployError("another deploy is running") from None
            f.write(f"{os.getpid()}\n")
            f.flush()
            yield

    def deploy(self, commit: str = BRANCH, action: str = "switch", actor: str = "", wait: bool = True,
               reason: str = "") -> dict:
        """Build COMMIT (on main) from its own release folder and activate it (`switch`), or only on next boot
        (`boot`), or only build it (`build`: nothing changes). Returns the deployment's record."""
        if action not in ("switch", "boot", "build"):
            raise DeployError("action is switch, boot or build")
        with self._lock(wait):
            sha = self.resolve(commit)
            where = self.release(sha)
            config = where / self.host()
            if not config.exists():
                raise DeployError(f"{sha[:12]} has no {self.host()}")
            before = self.current()
            started = time.time()
            p = subprocess.run([*self.rebuild, action, "-I", f"nixos-config={config}"])
            record = {"at": started, "seconds": round(time.time() - started, 1), "commit": sha, "action": action,
                      "ok": p.returncode == 0, "previous": before, "actor": actor or _actor(), "reason": reason,
                      "system": os.path.realpath("/run/current-system") if action == "switch" else None}
            if record["ok"] and action != "build":
                self._point(sha)
            self._record(record)
            if record["ok"]:
                self._prune()
            if not record["ok"]:
                raise DeployError(f"nixos-rebuild {action} of {sha[:12]} failed ({p.returncode}); the system still "
                                  f"runs {(before or 'what it ran')[:12]}")
            return record

    def rollback(self, actor: str = "", wait: bool = True) -> dict:
        """Deploy again the commit that was current before the current one."""
        now = self.current()
        earlier = [d["commit"] for d in self.history() if d["ok"] and d["action"] == "switch" and d["commit"] != now]
        if not earlier:
            raise DeployError("nothing to roll back to: no earlier successful deploy")
        return self.deploy(earlier[-1], actor=actor, wait=wait, reason=f"rollback from {(now or '?')[:12]}")

    def _record(self, record: dict) -> None:
        with open(self.state / "deployments.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")

    def history(self) -> list[dict]:
        try:
            lines = (self.state / "deployments.jsonl").read_text().splitlines()
        except OSError:
            return []
        return [json.loads(line) for line in lines if line.strip()]

    def status(self) -> dict:
        now = self.current()
        main = self._git("rev-parse", BRANCH)
        behind = int(self._git("rev-list", "--count", f"{now}..{main}")) if now else None
        text = self.nixos_config.read_text() if self.nixos_config.exists() else ""
        return {"current": now, "main": main, "main_ahead_by": behind, "host": self.host(),
                "configuration_uses_release": f"{self.state}/current/" in text and str(self.repo) + "/" not in text,
                "history": self.history()[-5:]}


def _actor() -> str:
    return os.environ.get("ODD1_TASKS_AGENT") or f"person:{os.environ.get('USER', 'unknown')}"


def _read_only(root: Path) -> None:
    for dirpath, dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            if not os.path.islink(p):
                os.chmod(p, os.stat(p).st_mode & ~0o222)
    for dirpath, dirs, _ in os.walk(root, topdown=False):
        for name in dirs:
            p = os.path.join(dirpath, name)
            if not os.path.islink(p):
                os.chmod(p, os.stat(p).st_mode & ~0o222)
    os.chmod(root, os.stat(root).st_mode & ~0o222)


def _remove(root: Path) -> None:
    for dirpath, dirs, _ in os.walk(root):
        os.chmod(dirpath, 0o755)
    shutil.rmtree(root, ignore_errors=True)
