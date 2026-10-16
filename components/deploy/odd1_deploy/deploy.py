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
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
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
        # The install's own settings (the person's values, machine overrides): a small private git repository whose
        # host.nix is built together with the release, never published. Optional.
        self.local = Path(env.get("ODD1_LOCAL", "/srv/odd1-local"))
        # After a switch: units that failed since, and the installed tests of the components the deploy changed.
        self.failed_units_cmd = shlex.split(env.get("ODD1_DEPLOY_FAILED_UNITS",
                                                    "systemctl --failed --plain --no-legend --no-pager"))
        self.tests_bin = Path(env.get("ODD1_DEPLOY_TESTS_BIN", "/run/current-system/sw/bin"))
        self.settle = float(env.get("ODD1_DEPLOY_SETTLE", 10))
        self.test_timeout = float(env.get("ODD1_DEPLOY_TEST_TIMEOUT", 900))
        # --force bypasses the nixos-rebuild guard (guard.py), so it calls the real one.
        self.force_rebuild = shlex.split(env.get("ODD1_DEPLOY_FORCE_REBUILD") or
                                         f"sudo -n {env.get('ODD1_DEPLOY_REAL_REBUILD', 'nixos-rebuild')}")

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
                if found == "odd1-config.nix":  # the composed file names no host: ask for it
                    break
                self.state.mkdir(parents=True, exist_ok=True)
                saved.write_text(found + "\n")
                return found
        raise DeployError(f"no host file: {self.nixos_config} imports none from {self.repo}; set ODD1_DEPLOY_HOST")

    # -- the install's local settings -----------------------------------------------------------------------------
    def has_local(self) -> bool:
        return (self.local / ".git").exists() and (self.local / "host.nix").exists()

    def local_head(self) -> str | None:
        if not self.has_local():
            return None
        p = subprocess.run(["git", "-C", str(self.local), "rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
                           capture_output=True, text=True)
        if p.returncode:
            raise DeployError(f"{self.local} has no commit yet: commit host.nix there first")
        return p.stdout.strip()

    def config_rel(self) -> str:
        """The file a release builds: the composed odd1-config.nix with a local config, else the host file."""
        return "odd1-config.nix" if self.has_local() else self.host()

    def composed(self, host_dir: str, local_dir: str) -> str:
        return (f"# Written by odd1-deploy: Odd1 (shared, the same for every install) plus this install's own settings.\n"
                f"{{ imports = [ {host_dir}/{self.host()} {local_dir}/host.nix ]; }}\n")

    def config_for(self, tree: Path) -> Path:
        """A configuration that builds a checkout (a task's worktree) with this install's local settings, for checks."""
        tree = Path(tree).resolve()
        if not self.has_local():
            return tree / self.host()
        out = self.state / "check-configs" / f"{hashlib.sha256(str(tree).encode()).hexdigest()[:16]}.nix"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.composed(str(tree), str(self.local)))
        return out

    @staticmethod
    def _export(repo: Path, sha: str, into: Path) -> None:
        archive = subprocess.Popen(["git", "-C", str(repo), "archive", "--format=tar", sha], stdout=subprocess.PIPE)
        untar = subprocess.run(["tar", "-x", "-C", str(into)], stdin=archive.stdout, capture_output=True)
        archive.stdout.close()
        if archive.wait() or untar.returncode:
            raise DeployError(f"could not export {sha[:12]} of {repo}: {untar.stderr.decode().strip()}")

    # -- releases -----------------------------------------------------------------------------------------------------
    def release(self, sha: str, local: str | None = None) -> Path:
        """<state>/releases/<sha>[-<local sha>]: exactly the commit's tree (git archive), and with a local config its
        committed host.nix under local/ plus odd1-config.nix joining them; read-only. Made once, then reused."""
        local = local or self.local_head()
        name = f"{sha}-{local[:12]}" if local else sha
        where = self.state / "releases" / name
        if (where / ".odd1-release").exists():
            return where
        where.parent.mkdir(parents=True, exist_ok=True)
        tmp = where.parent / f".tmp-{name}-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        try:
            self._export(self.repo, sha, tmp)
            if local:
                (tmp / "local").mkdir()
                self._export(self.local, local, tmp / "local")
                (tmp / "odd1-config.nix").write_text(self.composed(".", "./local"))
                (tmp / ".odd1-local").write_text(local + "\n")
        except DeployError:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
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

    def current_local(self) -> str | None:
        try:
            return (self.state / "current" / ".odd1-local").read_text().strip()
        except OSError:
            return None

    def _point(self, name: str, config_rel: str) -> None:
        """current -> releases/<sha> (atomically), and the system's configuration imports the host file through it."""
        link, tmp = self.state / "current", self.state / ".current.tmp"
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(Path("releases") / name)
        os.replace(tmp, link)
        want = (f"# Written by odd1-deploy: this machine runs the deployed release of the Odd1 repository (one commit,\n"
                f"# {link} -> releases/<sha>), never the development folder. Deploy with: odd1-deploy\n"
                f"{{ imports = [ {link}/{config_rel} ]; }}\n")
        if self.nixos_config.exists() and self.nixos_config.read_text() == want:
            return
        staged = self.state / "configuration.nix"
        staged.write_text(want)
        p = subprocess.run([*self.sudo, "install", "-m", "644", str(staged), str(self.nixos_config)],
                           capture_output=True, text=True)
        if p.returncode:
            raise DeployError(f"could not write {self.nixos_config}: {p.stderr.strip()}")

    def _prune(self) -> None:
        keep = {(self.state / "current").resolve().name} | {d.get("release", d["commit"]) for d in
                                                            self.history()[-KEEP:] if d["ok"]}
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
               reason: str = "", health: bool = True) -> dict:
        """Build COMMIT (on main) from its own release folder and activate it (`switch`), or only on next boot
        (`boot`), or only build it (`build`: nothing changes). After a switch the deploy proves itself (health): if
        a unit failed that was fine before, or a changed component's installed tests fail, it rolls back to the
        previous release on its own and says why. Returns the deployment's record."""
        if action not in ("switch", "boot", "build"):
            raise DeployError("action is switch, boot or build")
        with self._lock(wait):
            return self._deploy(self.resolve(commit), action, actor or _actor(), reason, health)

    def _deploy(self, sha: str, action: str, actor: str, reason: str, health: bool, local: str | None = None) -> dict:
        where = self.release(sha, local)
        local = (where / ".odd1-local").read_text().strip() if (where / ".odd1-local").exists() else None
        rel = "odd1-config.nix" if local else self.host()
        config = where / rel
        if not config.exists():
            raise DeployError(f"{sha[:12]} has no {rel}")
        before, before_local = self.current(), self.current_local()
        before_name = (self.state / "current").resolve().name if before else None
        failed_before = self.failed_units() if action == "switch" and health else set()
        started = time.time()
        p = subprocess.run([*self.rebuild, action, "-I", f"nixos-config={config}"])
        record = {"at": started, "seconds": round(time.time() - started, 1), "commit": sha, "local": local,
                  "release": where.name, "action": action, "ok": p.returncode == 0, "previous": before,
                  "actor": actor, "reason": reason,
                  "system": os.path.realpath("/run/current-system") if action == "switch" else None}
        if not record["ok"]:
            self._record(record)
            raise DeployError(f"nixos-rebuild {action} of {sha[:12]} failed ({p.returncode}); the system still "
                              f"runs {(before or 'what it ran')[:12]}")
        if action != "build":
            self._point(where.name, rel)
        if action == "switch" and health:
            record["health"] = self.health(failed_before, before, sha)
            if not record["health"]["ok"] and before and before_name != where.name:
                record["ok"], record["rolled_back_to"] = False, before
                self._record(record)
                why = self.why(record["health"])
                back = self._deploy(before, "switch", actor, f"automatic rollback from {sha[:12]}: {why}", False,
                                    before_local)
                raise DeployError(f"{sha[:12]} switched but is not healthy ({why}); rolled back to "
                                  f"{back['commit'][:12]}")
        self._record(record)
        if action == "switch":
            (self.state / "forced.json").unlink(missing_ok=True)  # a release runs again
        self._prune()
        return record

    # -- the person's override ----------------------------------------------------------------------------------------
    def force(self, reason: str, ask=input, say=print, at_terminal=None) -> dict:
        """Ship /srv/odd1's working tree as it is (uncommitted work included). Only a person at a terminal: stdin and
        stdout must be a terminal and they type a one-time code; no variable or flag does it. Logged; the next
        normal deploy returns to a release (the configuration still imports it)."""
        import secrets
        at_terminal = at_terminal or (lambda: sys.stdin.isatty() and sys.stdout.isatty())
        if not at_terminal():
            raise DeployError("--force needs a person at a terminal (it ships uncommitted work); agents deploy "
                              "committed work with odd1-deploy")
        dirty = subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"], capture_output=True,
                               text=True).stdout.splitlines()
        code = f"{secrets.randbelow(10000):04d}"
        say(f"This switches the system to {self.repo} as it is now, {len(dirty)} uncommitted changes included:")
        for line in dirty[:20]:
            say(f"  {line}")
        if ask(f"Type {code} to confirm: ").strip() != code:
            raise DeployError("not confirmed; nothing changed")
        reason = reason.strip() or ask("Why (kept in the deploy log): ").strip()
        if not reason:
            raise DeployError("say why (kept in the deploy log); nothing changed")
        with self._lock(True):
            config = self.config_for(self.repo)
            started = time.time()
            p = subprocess.run([*self.force_rebuild, "switch", "-I", f"nixos-config={config}"])
            record = {"at": started, "seconds": round(time.time() - started, 1), "commit": self._git("rev-parse",
                      "HEAD"), "action": "switch", "ok": p.returncode == 0, "previous": self.current(),
                      "actor": _actor(), "reason": reason, "forced": True, "dirty": dirty,
                      "terminal": _terminal(), "system": os.path.realpath("/run/current-system")}
            self._record(record)
            if record["ok"]:
                (self.state / "forced.json").write_text(json.dumps(record) + "\n")
            if not record["ok"]:
                raise DeployError(f"forced switch failed ({p.returncode})")
            return record

    # -- health after a switch ----------------------------------------------------------------------------------------
    def failed_units(self) -> set[str]:
        try:
            out = subprocess.run(self.failed_units_cmd, capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.TimeoutExpired):
            return set()
        return {line.split()[0] for line in out.splitlines() if line.split()}

    def changed_components(self, old: str | None, new: str) -> list[str]:
        if not old:
            return []
        names = self._git("diff", "--name-only", old, new).splitlines()
        return sorted({n.split("/")[1] for n in names if n.startswith("components/") and n.count("/") >= 2})

    def _test(self, component: str) -> int | None:
        test = self.tests_bin / f"odd1-{component}-test"
        if not os.access(test, os.X_OK):
            return None
        for _ in range(2):  # a failure is retried once: one flaky test should not undo a good deploy
            try:
                code = subprocess.run([str(test), "-q"], capture_output=True, timeout=self.test_timeout).returncode
            except subprocess.TimeoutExpired:
                code = 124
            if code in (0, 5):  # 5: no tests collected
                return 0
        return code

    def health(self, failed_before: set[str], old: str | None, new: str) -> dict:
        time.sleep(self.settle)  # units starting after the switch get a moment to fail or settle
        failed = sorted(self.failed_units() - failed_before)
        tests = {c: code for c in self.changed_components(old, new) if (code := self._test(c)) is not None}
        return {"ok": not failed and not any(tests.values()), "new_failed_units": failed, "tests": tests}

    @staticmethod
    def why(h: dict) -> str:
        parts = [f"units failed: {', '.join(h['new_failed_units'])}"] if h["new_failed_units"] else []
        parts += [f"odd1-{c}-test exited {code}" for c, code in h["tests"].items() if code]
        return "; ".join(parts)

    def rollback(self, actor: str = "", wait: bool = True) -> dict:
        """Deploy again the commit that was current before the current one."""
        now = self.current()
        name = (self.state / "current").resolve().name
        earlier = [d for d in self.history() if d["ok"] and d["action"] == "switch"
                   and d.get("release", d["commit"]) != name]
        if not earlier:
            raise DeployError("nothing to roll back to: no earlier successful deploy")
        with self._lock(wait):
            return self._deploy(earlier[-1]["commit"], "switch", actor or _actor(),
                                f"rollback from {(now or '?')[:12]}", False, earlier[-1].get("local"))

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
        forced = self.state / "forced.json"
        return {"forced": json.loads(forced.read_text()) if forced.exists() else None, "current": now,
                "local": self.current_local(), "local_head": self.local_head(), "main": main,
                "main_ahead_by": behind, "host": self.host(),
                "configuration_uses_release": f"{self.state}/current/" in text and str(self.repo) + "/" not in text,
                "history": self.history()[-5:]}


def _terminal() -> str:
    try:
        return os.ttyname(0)
    except OSError:
        return "?"


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
