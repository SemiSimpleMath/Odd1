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

from . import describe_pixels, screenshot

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


def ocr(png: str = "", window_id: str = "", max_words: int = 2000, display: str = "user") -> dict:
    """OCR a PNG file, or capture a window (X11 id, even when covered) or the whole screen first."""
    if png:
        return describe_pixels.describe_image(png, max_words=max_words)
    with tempfile.TemporaryDirectory(prefix="any-app-ocr-") as tmp:
        target = Path(tmp) / "capture.png"
        if window_id:
            screenshot.capture(window_id, target, display_name=display)
        else:
            screenshot.capture_screen(target, display_name=display)
        return describe_pixels.describe_image(target, max_words=max_words)


@mcp.tool()
def app_describe(window: str, max_elements: int = 1000) -> dict:
    """Describe one window from its accessibility tree: {"window", "elements": [{"id", "role", "name", "bounds",
    "states"}...], "truncated"}. window: a case-insensitive substring of its title that matches exactly one window."""
    return describe(window, max_elements)


@mcp.tool()
def app_ocr(png: str = "", window_id: str = "", max_words: int = 2000, display: str = "user") -> dict:
    """Read visible words from pixels with local Tesseract: {"elements": [{"text", "confidence", "bounds"}...]}.
    Give a PNG path, or an X11 window id (e.g. from `xdotool search --name`), or nothing for the whole screen.
    Boxes are image-local pixels. display: "user" (the person's screen) or "agent" (the agent desktop)."""
    return ocr(png, window_id, max_words, display)


@mcp.tool()
def app_screenshot(window: str = "", out: str = "", frame: bool = False, display: str = "user") -> dict:
    """Screenshot one window to a PNG, even when other windows cover it, or the whole screen: {"path", "window",
    "title", "app", "width", "height", "sha256"}. window: a title or app-name substring matching exactly one
    window, or an X11 id; empty for the whole screen (window "root"; frame is ignored); out: PNG path
    (default: a new file under $TMPDIR/odd1-any-app); frame: include the window manager's title bar and border.
    Read the PNG at "path" to see it. Minimized windows have no pixels and are an error. display: "user" (the
    person's screen) or "agent" (the agent desktop, where agents run their own apps)."""
    if not window:
        return screenshot.capture_screen(out or None, display_name=display)
    return screenshot.capture(window, out or None, frame, display_name=display)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
