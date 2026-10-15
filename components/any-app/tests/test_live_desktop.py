"""Live checks on the Odd1 Desktop: OCR on disposable Tk fixtures. Skipped without a display and the tools."""
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent


def _missing():
    if not os.environ.get('DISPLAY'):
        return 'needs the Odd1 Desktop (DISPLAY unset)'
    absent = [tool for tool in ('tesseract', 'import') if shutil.which(tool) is None]
    if absent:
        return 'needs ' + ', '.join(absent)
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return 'needs tkinter'
    return None


@pytest.mark.skipif(_missing() is not None, reason=_missing() or '')
def test_ocr_matrix_on_tk_fixtures():
    done = subprocess.run([sys.executable, str(HERE / 'live_pixel_matrix.py')],
                          capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    assert '"passed":true' in done.stdout
