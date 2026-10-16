"""The wiki MCP server: search Odd1's knowledge base and read its pages."""
from mcp.server.fastmcp import FastMCP

from . import wiki

mcp = FastMCP("wiki")


@mcp.tool()
def wiki_search(query: str) -> list:
    """Search Odd1's wiki: sections matching all the words, each with its page, section, status (active rule, page,
    superseded) and the matching line. Active decision records come first. Read a page with wiki_page."""
    return wiki.search(query)


@mcp.tool()
def wiki_page(page: str) -> str:
    """A wiki page (e.g. "how-a-change-ships.md", "decisions/0003-the-persons-data-is-never-destroyed.md")."""
    try:
        return wiki.show(page)
    except FileNotFoundError as e:
        return str(e)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
