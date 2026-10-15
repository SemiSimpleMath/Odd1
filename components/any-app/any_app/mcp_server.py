"""The any-app agent entry point: look at any window on the Odd1 Desktop as MCP tools (stdio).

app_describe reads a window's accessibility tree (GTK, Qt, LibreOffice); app_ocr reads text from pixels when an
app exposes no tree (X11/Xt, games, canvases). Both are observations only: their boxes are not yet calibrated
screen coordinates, so act through an app's own interface or the desktop's input tools with care.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import describe_pixels

mcp = FastMCP("any-app")
MAX_BYTES = 262144


def describe(window: str, max_elements: int = 1000, timeout: float = 20) -> dict:
    """Run the AT-SPI describer in a child process: a stuck accessibility bus cannot hang the server."""
    script = Path(__file__).with_name("describe_accessible.py")
    done = subprocess.run([sys.executable, str(script), window, "--max-elements", str(max_elements),
                           "--max-bytes", str(MAX_BYTES)], capture_output=True, timeout=timeout)
    if done.returncode:
        raise RuntimeError(done.stderr.decode("utf-8", "replace").strip()[:1000] or "describe failed")
    return json.loads(done.stdout)


def ocr(png: str = "", window_id: str = "", max_words: int = 2000) -> dict:
    """OCR a PNG file, or capture a window (X11 id) or the whole screen with ImageMagick `import` first."""
    if png:
        return describe_pixels.describe_image(png, max_words=max_words)
    with tempfile.TemporaryDirectory(prefix="any-app-ocr-") as tmp:
        target = Path(tmp) / "capture.png"
        subprocess.run(["import", "-window", window_id or "root", f"png:{target}"], check=True,
                       capture_output=True, timeout=10)
        return describe_pixels.describe_image(target, max_words=max_words)


@mcp.tool()
def app_describe(window: str, max_elements: int = 1000) -> dict:
    """Describe one window from its accessibility tree: {"window", "elements": [{"id", "role", "name", "bounds",
    "states"}...], "truncated"}. window: a case-insensitive substring of its title that matches exactly one window."""
    return describe(window, max_elements)


@mcp.tool()
def app_ocr(png: str = "", window_id: str = "", max_words: int = 2000) -> dict:
    """Read visible words from pixels with local Tesseract: {"elements": [{"text", "confidence", "bounds"}...]}.
    Give a PNG path, or an X11 window id (e.g. from `xdotool search --name`), or nothing for the whole screen.
    Boxes are image-local pixels."""
    return ocr(png, window_id, max_words)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
