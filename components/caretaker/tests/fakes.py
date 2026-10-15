"""A stand-in for subprocess.run that answers by command, and a fake /proc + /sys/fs/cgroup tree."""
import subprocess
from pathlib import Path


class Runner:
    def __init__(self, answers):
        self.answers = answers  # list of (predicate on argv, (returncode, stdout, stderr))
        self.calls = []

    def __call__(self, argv, **_kw):
        self.calls.append(list(argv))
        for match, (code, out, err) in self.answers:
            if match(argv):
                return subprocess.CompletedProcess(argv, code, out, err)
        return subprocess.CompletedProcess(argv, 1, "", "unexpected command")


PSI_CALM = "some avg10=0.50 avg60=0.20 avg300=0.10 total=1000\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=10\n"
PSI_HOT = "some avg10=55.00 avg60=30.00 avg300=9.00 total=99999\nfull avg10=12.00 avg60=4.00 avg300=1.00 total=5000\n"


def machine(root: Path, fg_pressure=PSI_HOT):
    proc, cg = root / "proc", root / "cgroup"
    (proc / "pressure").mkdir(parents=True)
    for name in ("cpu", "memory", "io"):
        (proc / "pressure" / name).write_text(PSI_CALM)
    (proc / "loadavg").write_text("0.50 0.40 0.30 1/200 12345\n")
    (proc / "meminfo").write_text("MemTotal:  8000000 kB\nMemAvailable:  400000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    p = proc / "4242"
    p.mkdir()
    (p / "comm").write_text("soffice.bin\n")
    (p / "cmdline").write_text("/usr/bin/soffice\0--calc\0")
    (p / "status").write_text("Name:\tsoffice.bin\nVmRSS:\t  512000 kB\n")
    (p / "cgroup").write_text("0::/user.slice/user-1000.slice/session-2.scope\n")
    d = cg / "user.slice/user-1000.slice/session-2.scope"
    d.mkdir(parents=True)
    (d / "memory.current").write_text("600000000\n")
    (d / "memory.max").write_text("max\n")
    (d / "memory.pressure").write_text(fg_pressure)
    (d / "cpu.pressure").write_text(PSI_CALM)
    return proc, cg


def desktop_runner(failed="nginx.service loaded failed failed The nginx HTTP server\n"):
    return Runner([
        (lambda a: a[0] == "xdotool", (0, "4242\n", "")),
        (lambda a: "list-units" in a, (0, failed, "")),
    ])
