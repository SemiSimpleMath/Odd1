"""On a live Linux machine with PSI (Odd1): one real look must produce sane numbers. Skipped elsewhere."""
import shutil
import sys
from pathlib import Path

import pytest

from odd1_caretaker import probe

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux") or not Path("/proc/pressure/memory").exists(),
                                reason="needs Linux with PSI (/proc/pressure); runs on the Odd1 VM")


def test_real_look():
    snap = probe.snapshot()
    assert snap["memory"]["MemTotal"] > 0 and snap["load"] is not None
    assert snap["pressure"]["memory"]["some"]["avg10"] >= 0
    if shutil.which("systemctl"):
        assert isinstance(snap["failed_units"], list), snap.get("failed_units_unavailable")
