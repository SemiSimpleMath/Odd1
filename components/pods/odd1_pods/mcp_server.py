"""The pods MCP server: content by reference for every agent; secrets handled through the courier, never read."""
from mcp.server.fastmcp import FastMCP

from . import courier
from .store import PodError, Pods

mcp = FastMCP("pods")


def _do(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except PodError as e:
        return {"error": str(e)}


@mcp.tool()
def pod_put(kind: str, text: str = "", file: str = "", one_liner: str = "", tags: list[str] = [],
            sources: list[str] = []) -> dict:
    """Store content as a pod and get its reference (pod:<kind>:<id>) to pass to others instead of the content.
    Kinds: note, file, image, audio, video, chat, skill, report (secrets are stored by a person, not here)."""
    return _do(lambda: {"uri": Pods().put(kind, text, one_liner=one_liner, tags=tags, file=file or None,
                                          sources=sources)})


@mcp.tool()
def pod_show(uri: str) -> dict:
    """A pod's header: kind, one-liner, tags, authority, where it came from, which projections exist. No content."""
    return _do(Pods().header, uri)


@mcp.tool()
def pod_get(uri: str, projection: str = "body") -> dict:
    """A pod's content (projection body), or a secret's last4/length. Secrets' values are never returned: hand
    their reference to pod_http or pod_run."""
    return _do(lambda: {"uri": uri, "projection": projection, "value": Pods().get(uri, projection)})


@mcp.tool()
def pod_find(kind: str = "", tag: str = "", text: str = "") -> list:
    """Pods by kind, tag or text in their one-liner or body (headers only)."""
    return Pods().find(kind or None, tag or None, text or None)


@mcp.tool()
def pod_http(method: str, url: str, headers: dict = {}, body: str = "") -> dict:
    """An HTTPS request with {{pod:...}} placeholders in headers or body resolved by the courier (each secret only for
    a host its pod allows). The reply comes back with every secret scrubbed out."""
    return _do(courier.http, Pods(), method, url, headers, body)


@mcp.tool()
def pod_run(command: list[str], env: dict, cwd: str = "") -> dict:
    """Run a command with environment variables set from secret pods ({"OPENAI_API_KEY": "pod:secret.token:..."}),
    each only if its pod allows env:<VAR>. Output comes back scrubbed."""
    return _do(courier.run, Pods(), command, env, cwd or None)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
