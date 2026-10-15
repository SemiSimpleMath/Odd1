"""The caretaker's agent entry point (MCP, stdio): the machine's health, pressure, and repair.

caretaker_status reads what the daemon last recorded (cheap, no probing); caretaker_pressure looks now.
"""
from mcp.server.fastmcp import FastMCP

from . import probe, repair, status

mcp = FastMCP("caretaker")


@mcp.tool()
def caretaker_status(max_age_seconds: int = 180) -> dict:
    """Is the caretaker current, and what did it last see? Returns its freshness (healthy/stale/unhealthy/unknown),
    its service state, the overall pressure level (ok/elevated/high), plain-language problems (failed units, low
    memory, full disks, pressure), the foreground program, and the most recent repairs."""
    return status.status(max_age_seconds=max_age_seconds)


@mcp.tool()
def caretaker_pressure() -> dict:
    """Resource pressure right now: system PSI for cpu, memory and io (percent of time tasks stalled, avg10/60/300)
    and the focused window's program with its memory use and its cgroup's pressure. level: ok/elevated/high."""
    return probe.pressure()


@mcp.tool()
def caretaker_repair(unit: str) -> dict:
    """Restart a systemd unit (e.g. "nginx.service") and verify it is active again. Recorded in the caretaker's
    actions log. Returns ok, the unit's state after the restart, and the error if it did not come back."""
    return repair.restart(unit, reason="agent request")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
