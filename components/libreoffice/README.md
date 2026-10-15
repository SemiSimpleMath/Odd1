# libreoffice
Drive LibreOffice (Calc first) through its own object model, UNO, on the visible desktop: exact values, no screen guessing.
Milestone: ★ (any app, through interfaces agents write themselves). This is the first per-app adapter and the pattern for more.

## Use
- Agents: the MCP server `libreoffice` (registered for every agent): `calc_open`, `calc_read`, `calc_write`,
  `calc_formula`, `calc_save`, `calc_close`. Documents are named by their file path.
- Command line: `odd1-calc read FILE [--range A1:C9]`, `odd1-calc write FILE SHEET '[["a",1]]' --save OUT`,
  `odd1-calc formula FILE SHEET D2 '=AVERAGE(B2:B9)'`, `odd1-calc open FILE` (leaves it on screen).
- Python: `PYTHONPATH=$ODD1_OFFICE_PYTHONPATH python3`, then `from odd1_office.calc import Calc`;
  `Calc.connect()` attaches to the running Calc (UNO on 127.0.0.1:2002) or starts one on DISPLAY :0.
  `open`, `new`, `read`, `write`, `formula`, `save`, `close`.
- Documents open visibly unless `hidden=True`, so a person can watch at the desktop's live view.

## Tests
`odd1-calc-test` on Odd1 (needs LibreOffice): loads people and heights, finds the unique people, writes their
average heights to a Summary sheet, checks them against Calc's own AVERAGEIF, and saves as .ods.

## Next
Writer and Impress (same UNO session), named ranges and charts, then a screen-model link: which window shows which
document, so other agents can find it.
