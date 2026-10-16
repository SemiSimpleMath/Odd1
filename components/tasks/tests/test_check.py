"""odd1-tasks-check: when the system does not build, it says which derivation failed and how its log ends, not the
hundreds of paths Nix would have built."""
import os
import subprocess
import sys
from pathlib import Path

CHECK = Path(__file__).resolve().parents[1] / "scripts" / "check.sh"
DRV = "/nix/store/" + "a" * 32 + "-python3.13-selenium-4.51.0.drv"


def stub(bin_dir, name, body):
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def test_check_shows_build_error(tmp_path):
    wt = tmp_path / "wt"
    (wt / "tools").mkdir(parents=True)
    (wt / "tools" / "check.py").write_text("")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], check=True)
    subprocess.run([*git, "-C", str(wt), "add", "-A"], check=True)
    subprocess.run([*git, "-C", str(wt), "commit", "-qm", "x"], check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)
    stub(bin_dir, "odd1-deploy", f"echo {tmp_path}/configuration.nix\n")
    built = "".join(f"echo '  /nix/store/{i:032d}-dep-{i}.drv' >&2\n" for i in range(300))
    stub(bin_dir, "nix-build", "echo 'these 301 derivations will be built:' >&2\n" + built + f"""
echo "building '{DRV}'..." >&2
echo "error: builder for '{DRV}' failed with exit code 1;" >&2
echo "error: 1 dependencies of derivation '/nix/store/{'b' * 32}-nixos-system.drv' failed to build" >&2
exit 1
""")
    log = "".join(f"echo 'log line {i}'\n" for i in range(100))
    stub(bin_dir, "nix", f'[ "$1 $2" = "log {DRV}" ] || exit 1\n' + log + "echo 'ERROR: test_selenium failed'\n")
    env = {"PATH": f"{bin_dir}:/run/current-system/sw/bin:/usr/bin:/bin", "ODD1_TASKS_WORKTREE": str(wt),
           "HOME": str(tmp_path)}
    done = subprocess.run(["bash", str(CHECK)], cwd=wt, capture_output=True, text=True, env=env)
    out = done.stdout + done.stderr
    assert done.returncode == 1 and "check passed" not in out
    assert f"builder for '{DRV}' failed" in out and f"failed: {DRV}" in out
    assert "ERROR: test_selenium failed" in out and "log line 99" in out  # the end of its build log
    assert "log line 10\n" not in out  # only the end
    assert "will be built" not in out and "-dep-" not in out
    assert len(out.splitlines()) < 100
