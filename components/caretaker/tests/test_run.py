"""Measured runs under limits (systemd-run and systemctl faked)."""
import io
import subprocess

import pytest

from odd1_caretaker import run


class FakeProc:
    def __init__(self, code=0, polls=2):
        self.code, self.polls, self.returncode = code, polls, None

    def communicate(self, timeout=None):
        self.polls -= 1
        if self.polls > 0:
            raise subprocess.TimeoutExpired("x", timeout)
        self.returncode = self.code
        return "child out\n", "child err\n"


def _measure(result="success", code=0, peak="52428800"):
    shows = [f"ActiveState=active\nMemoryPeak={peak}\nTasksCurrent=7\nResult=success\n",
             f"ActiveState=inactive\nMemoryPeak={peak}\nTasksCurrent=0\nResult={result}\n"]
    calls, popen_argv = [], []

    def answer(argv, **_kw):
        calls.append(argv)
        if "show" in argv:
            text = shows[min(sum("show" in c for c in calls) - 1, 1)]
            return subprocess.CompletedProcess(argv, 0, text, "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def popen(argv, **_kw):
        popen_argv.extend(argv)
        return FakeProc(code=code)

    out, err = io.StringIO(), io.StringIO()
    report = run.run_measured(["python3", "app.py"], run.Limits(memory_mib=512), popen=popen, run=answer,
                              sleep=lambda s: None, out=out, err=err)
    return report, popen_argv, out.getvalue(), err.getvalue(), calls


def test_success_measures_and_passes_output_through():
    report, argv, out, err, _ = _measure()
    assert argv[0] == "systemd-run" and "--property=MemoryMax=512M" in argv and argv[-2:] == ["python3", "app.py"]
    assert out == "child out\n" and err.startswith("child err\n")
    assert report["outcome"] == "success" and report["memory_peak_bytes"] == 52428800 and report["tasks_high"] == 7


def test_oom_is_named_and_unit_reset():
    report, _, _, _, calls = _measure(result="oom-kill", code=137)
    assert report["outcome"] == "resource-oom"
    assert any("reset-failed" in c for c in calls)


def test_no_telemetry_is_not_success():
    report, *_ = _measure(peak="[not set]")
    assert report["outcome"] == "telemetry-incomplete"


def test_outcome_and_limits():
    assert run.outcome("timeout", 1, True) == "runtime-limit"
    assert run.outcome("success", 2, True) == "command-failed"
    assert run.outcome("exit-code", 2, True) == "systemd-failed"
    with pytest.raises(ValueError):
        run.Limits(memory_mib=1).validate()
    with pytest.raises(ValueError):
        run.run_measured([], popen=None)
