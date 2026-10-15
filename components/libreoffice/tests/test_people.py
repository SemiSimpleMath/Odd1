"""The user's example, end to end in real Calc: load data, find the unique people, compute their average heights.
Runs on Odd1 (needs LibreOffice): odd1-calc-test, or PYTHONPATH as in module.nix then python -m pytest."""
import csv
import os
import tempfile

from odd1_office.calc import Calc

ROWS = [("Ana", 170), ("Ben", 182), ("Ana", 173), ("Cleo", 165), ("Ben", 180), ("Dev", 177), ("Cleo", 167)]


def test_unique_people_and_average_heights():
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "people.csv")
    with open(src, "w", newline="") as f:
        csv.writer(f).writerows([("name", "height_cm"), *ROWS])
    calc = Calc.connect()
    doc = calc.open(src, hidden=True)
    try:
        header, *data = calc.read(doc)
        assert header == ["name", "height_cm"]
        heights: dict[str, list[float]] = {}
        for name, h in data:
            heights.setdefault(name, []).append(float(h))
        summary = [["name", "average_height_cm"]] + [[n, sum(v) / len(v)] for n, v in sorted(heights.items())]
        assert calc.write(doc, "Summary", summary) == "A1:B5"
        assert calc.read(doc, "Summary") == [["name", "average_height_cm"], ["Ana", 171.5], ["Ben", 181.0],
                                             ["Cleo", 166.0], ["Dev", 177.0]]
        # Calc's own arithmetic agrees.
        data_sheet = doc.Sheets.getByIndex(0).Name  # a CSV loads as a sheet named after the file
        f = f'=AVERAGEIF(${data_sheet}.A2:A8;"Ana";${data_sheet}.B2:B8)'
        assert calc.formula(doc, "Summary", "D2", f) == 171.5
        out = calc.save(doc, os.path.join(tmp, "people.ods"))
        assert os.path.getsize(out) > 0
    finally:
        calc.close(doc)
