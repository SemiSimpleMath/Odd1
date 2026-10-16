"""The tests run their own Calc on the agent desktop (:1), never on the person's screen (:0), on their own port
and profile so they neither attach to nor collide with a Calc someone is using; it is stopped afterwards."""
import os
import signal
import tempfile

import pytest

os.environ["DISPLAY"] = os.environ.get("ODD1_AGENT_DISPLAY") or ":1"
os.environ.setdefault("ODD1_CALC_PORT", "2012")
os.environ.setdefault("ODD1_CALC_PROFILE", tempfile.mkdtemp(prefix="odd1-calc-test-"))


@pytest.fixture(scope="session", autouse=True)
def calc_on_agent_desktop():
    if not os.path.exists(f"/tmp/.X11-unix/X{os.environ['DISPLAY'].lstrip(':').split('.')[0]}"):
        pytest.fail(f"the agent desktop {os.environ['DISPLAY']} is not running (odd1-agent-desktop status)")
    from odd1_office.calc import Calc
    calc = Calc.connect(timeout=60)
    yield calc
    try:
        calc.desktop.terminate()
    except Exception:
        pass  # the bridge drops as soffice exits
    if Calc.process is not None:
        try:
            Calc.process.wait(timeout=15)
        except Exception:
            os.killpg(Calc.process.pid, signal.SIGKILL)  # oosplash and soffice.bin share the session
            Calc.process.wait()
