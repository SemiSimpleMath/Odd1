"""A profile lock left by another host name (the machine was renamed) does not stop Calc from starting unattended;
LibreOffice would otherwise wait on its 'personal settings are locked' dialog."""
import os
import shutil
import signal
import tempfile

import pytest

from odd1_office import calc as calc_module
from odd1_office.calc import Calc

STALE = ("[Lockdata]\nUser=agent\nHost=orbit-desktop\nStamp=BF33333BBDE34B236477B4D55B29F050\n"
         "Time=Fri Oct 16 04:00:00 2026\nIPCServer=true\n")


@pytest.fixture
def own_calc(monkeypatch):
    profile = tempfile.mkdtemp(prefix="odd1-calc-lock-")
    with open(os.path.join(profile, ".lock"), "w") as f:
        f.write(STALE)
    port = int(os.environ["ODD1_CALC_PORT"]) + 1
    monkeypatch.setattr(calc_module, "PROFILE", profile)
    monkeypatch.setattr(calc_module, "PORT", port)
    monkeypatch.setattr(calc_module, "ACCEPT", f"socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext")
    monkeypatch.setattr(Calc, "process", None)
    yield profile
    process = Calc.process
    if process is not None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    shutil.rmtree(profile, ignore_errors=True)


def test_stale_lock_from_another_host(own_calc):
    calc = Calc.connect(timeout=60)
    doc = calc.new(hidden=True)
    try:
        assert calc.write(doc, 0, [["ok"]]) == "A1:A1"
    finally:
        calc.close(doc)
        try:
            calc.desktop.terminate()
        except Exception:
            pass
