"""The libreoffice component's agent entry point: Calc as MCP tools (stdio).

Any MCP-speaking agent (Claude Code, Orbit's agents) can open a spreadsheet, read and write ranges, compute with
Calc's own formulas and save, on the visible desktop. Documents are named by their file path.
"""
from mcp.server.fastmcp import FastMCP

from .calc import Calc

mcp = FastMCP("libreoffice")
_calc: Calc | None = None
_docs: dict = {}


def _doc(path: str, visible: bool = True):
    global _calc
    if _calc is None:
        _calc = Calc.connect()
    if path not in _docs:
        _docs[path] = _calc.open(path, hidden=not visible)
    return _docs[path]


@mcp.tool()
def calc_open(path: str, visible: bool = True) -> dict:
    """Open a spreadsheet file (ods, xlsx, csv, ...) in LibreOffice Calc; visible on the desktop unless visible=False.
    Returns its sheet names."""
    doc = _doc(path, visible)
    return {"path": path, "sheets": list(doc.Sheets.getElementNames())}


@mcp.tool()
def calc_read(path: str, sheet: str = "", cell_range: str = "") -> dict:
    """Read a range like "A1:C20" (or the sheet's whole used area when empty) as {"rows": [[...], ...]} of strings
    and numbers. sheet: a sheet name; empty means the first sheet."""
    doc = _doc(path)
    return {"rows": _calc_ref().read(doc, sheet or 0, cell_range or None)}


@mcp.tool()
def calc_write(path: str, sheet: str, rows: list[list], top_left: str = "A1") -> str:
    """Write rows (strings and numbers) into a sheet (created if missing), starting at top_left. Returns the range."""
    return _calc_ref().write(_doc(path), sheet, rows, top_left)


@mcp.tool()
def calc_formula(path: str, sheet: str, cell: str, formula: str) -> str | float:
    """Put a Calc formula in a cell (e.g. '=AVERAGEIF($people.A2:A9;"Ana";$people.B2:B9)'; ';' separates arguments)
    and return the value Calc computes, or the error text it shows."""
    return Calc.formula(_doc(path), sheet, cell, formula)


@mcp.tool()
def calc_save(path: str, save_as: str = "") -> str:
    """Save the document, to save_as if given (its extension picks the format: ods, xlsx, csv). Returns the path."""
    return Calc.save(_doc(path), save_as or path)


@mcp.tool()
def calc_close(path: str) -> str:
    """Close the document without saving."""
    doc = _docs.pop(path, None)
    if doc is not None:
        Calc.close(doc)
    return "closed"


def _calc_ref() -> Calc:
    assert _calc is not None
    return _calc


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
