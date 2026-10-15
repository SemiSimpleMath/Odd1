"""Make the component importable when tests run from anywhere (python -m pytest components/browser/tests)."""
import sys
from pathlib import Path

COMPONENT = Path(__file__).resolve().parent.parent
if str(COMPONENT) not in sys.path:
    sys.path.insert(0, str(COMPONENT))
# Subprocesses (the CLI tests) find the package the same way.
import os  # noqa: E402

os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (str(COMPONENT), os.environ.get("PYTHONPATH")) if p)
