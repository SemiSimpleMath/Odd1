"""odd1-calc: LibreOffice Calc from the command line, through UNO, on the visible desktop.

    odd1-calc read FILE [--sheet NAME] [--range A1:C9]      print rows as JSON
    odd1-calc write FILE SHEET JSON_ROWS [--at A1] [--save OUT]
    odd1-calc formula FILE SHEET CELL '=AVERAGE(B2:B9)' [--save OUT]
    odd1-calc open FILE                                       open it on screen and leave it open

The document opens visibly on DISPLAY :0 (people can watch); --hidden keeps it off screen.
"""
import argparse
import json
import sys

from .calc import Calc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="odd1-calc")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("read", "write", "formula", "open"):
        p = sub.add_parser(name)
        p.add_argument("file")
        p.add_argument("--hidden", action="store_true")
        if name == "read":
            p.add_argument("--sheet", default=0)
            p.add_argument("--range")
        if name in ("write", "formula"):
            p.add_argument("sheet")
        if name == "write":
            p.add_argument("rows", help="JSON list of rows")
            p.add_argument("--at", default="A1")
        if name == "formula":
            p.add_argument("cell")
            p.add_argument("formula")
        if name in ("write", "formula"):
            p.add_argument("--save")
    a = ap.parse_args(argv)
    calc = Calc.connect()
    doc = calc.open(a.file, hidden=a.hidden)
    if a.cmd == "read":
        sheet = int(a.sheet) if str(a.sheet).isdigit() else a.sheet
        print(json.dumps(calc.read(doc, sheet, a.range)))
    elif a.cmd == "write":
        print(calc.write(doc, a.sheet, json.loads(a.rows), a.at))
    elif a.cmd == "formula":
        print(calc.formula(doc, a.sheet, a.cell, a.formula))
    if getattr(a, "save", None):
        calc.save(doc, a.save)
    if a.cmd != "open" and a.hidden:
        calc.close(doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
