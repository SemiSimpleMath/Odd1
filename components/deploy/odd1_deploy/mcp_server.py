"""odd1-deploy's agent entry point (MCP, stdio): what is deployed, deploy an approved commit, roll back."""
from mcp.server.fastmcp import FastMCP

from .deploy import Deployer, DeployError

mcp = FastMCP("deploy")


@mcp.tool()
def deploy_status() -> dict:
    """The deployed commit (current), main's commit and how many commits it is ahead, whether the system's
    configuration imports the deployed release, and the last five deploys."""
    return Deployer().status()


@mcp.tool()
def deploy_commit(commit: str = "main", action: str = "switch", reason: str = "") -> dict:
    """Build COMMIT (it must be on main) from its own clean release folder and activate it: action switch (now),
    boot (next boot) or build (only build). Waits for a running deploy. Uncommitted edits never ship."""
    try:
        return Deployer().deploy(commit, action, reason=reason)
    except DeployError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def deploy_rollback() -> dict:
    """Deploy again the commit that was current before the current one."""
    try:
        return Deployer().rollback()
    except DeployError as e:
        return {"ok": False, "error": str(e)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
