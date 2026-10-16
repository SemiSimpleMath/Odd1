"""The stats agent entry point (MCP, stdio): is agent performance degrading, and where is the friction?"""
from mcp.server.fastmcp import FastMCP

from . import collect, report

mcp = FastMCP("stats")


@mcp.tool()
def stats_summary(days: float = 7) -> dict:
    """Totals over the last `days` days from the 15-minute samples: per task role (runs, done/retry/parked/blocked,
    budget hits, turns, cost, minutes), Claude Code usage by source (routine task runs vs chats: sessions, turns,
    tokens, tool calls and errors, permission denials, interrupts), tool errors per tool and MCP server, friction
    tickets per component, and the machine (pressure, memory, disks, failed units, deploys and rollbacks)."""
    return report.summary(collect.paths()["state"], days)


@mcp.tool()
def stats_trend(days: float = 7) -> dict:
    """The last `days` days against the `days` before: success rate, turns/cost/minutes per done task, budget hits,
    tool-error rate overall and per tool, denials, interrupts, friction tickets. verdict WORSE marks a regression
    (20% or more, with enough data); `regressions` lists them."""
    return report.trend(collect.paths()["state"], days)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
