"""Live broker checks: a real private Xvfb session with the Tk form app, driven end to end.

Each runs one script from tests/live/ and needs Linux with Xvfb, xauth, xdpyinfo, xdotool, ImageMagick `import`,
util-linux `prlimit` and tkinter (the Odd1 Desktop has them). Skipped elsewhere. The aggregate-limits probes
(live_recovery_smoke.py, abrupt_recovery_probe.py) create user-systemd slices and are run by hand on the Desktop.
"""
import pathlib
import shutil
import subprocess
import sys

import pytest

LIVE = pathlib.Path(__file__).resolve().parent / 'live'
TOOLS = ('Xvfb', 'xauth', 'xdpyinfo', 'xdotool', 'import', 'prlimit')


def _missing():
    if not sys.platform.startswith('linux'):
        return 'needs Linux (Unix sockets, Xvfb)'
    absent = [tool for tool in TOOLS if shutil.which(tool) is None]
    if absent:
        return 'needs the Odd1 Desktop GUI tools; missing: ' + ', '.join(absent)
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return 'needs tkinter for the form app'
    return None


pytestmark = pytest.mark.skipif(_missing() is not None, reason=_missing() or '')


@pytest.mark.parametrize('script', ['smoke.py', 'broker_capacity_probe.py', 'broker_load_probe.py'])
def test_live_script(script):
    done = subprocess.run([sys.executable, str(LIVE / script)], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-2000:] or done.stdout[-2000:]
