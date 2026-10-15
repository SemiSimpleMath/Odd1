"""The agent desktop's agent entry point (MCP, stdio): start apps on a display of their own, out of the person's
way, then read and drive them with the screen component's tools (display="agent", ids a1, a2, ...)."""
from mcp.server.fastmcp import FastMCP

from . import desktop

mcp = FastMCP("agent-desktop")


@mcp.tool()
def desktop_run(command: str, wait_s: float = 10) -> dict:
    """Start a program on the agent desktop (e.g. "gnome-calculator", "libreoffice --calc report.ods"): its own
    display, pointer and keyboard, so working there never disturbs the person. Returns {unit, windows}; then use
    screen_view(display="agent") and screen_click / screen_type on its ids."""
    return desktop.run(command, wait_s=wait_s)


@mcp.tool()
def desktop_status() -> dict:
    """Whether the agent desktop runs, its windows, and the programs agents started there."""
    return desktop.status()


@mcp.tool()
def desktop_stop(unit: str) -> dict:
    """Stop a program started with desktop_run (its unit name)."""
    return desktop.stop(unit)


@mcp.tool()
def desktop_show() -> dict:
    """Bring the agent desktop up on the person's screen, so they can watch or take over."""
    return desktop.show()


@mcp.tool()
def desktop_hide() -> dict:
    """Minimize the agent desktop on the person's screen; its apps keep running and stay drivable."""
    return desktop.hide()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
