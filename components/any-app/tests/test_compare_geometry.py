import importlib.util
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "any_app" / "compare_geometry.py"
spec = importlib.util.spec_from_file_location("compare_geometry", SCRIPT)
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


def png(width=100, height=80):
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00" + b"X" * 12


class GeometryEvidenceTests(unittest.TestCase):
    def test_parse_wmctrl_preserves_titles_with_spaces_and_bad_lines(self):
        rows = compare.parse_wmctrl("0x10 0 11 22 300 200 host My Window Title\nbroken\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "My Window Title")
        self.assertEqual(rows[0]["x"], 11)

    def test_png_dimensions_reject_invalid_data(self):
        self.assertEqual(compare.png_dimensions(png()), (100, 80))
        with self.assertRaises(ValueError):
            compare.png_dimensions(b"not png")
        with self.assertRaises(ValueError):
            compare.png_dimensions(png(0, 80))

    def test_geometry_comparison_is_explicit_and_tolerance_scoped(self):
        same = {"x": 10, "y": 20, "width": 300, "height": 200}
        result = compare.compare_rectangles(same, dict(same, x=12), tolerance=2)
        self.assertTrue(result["within_tolerance"])
        self.assertEqual(result["delta_atspi_minus_x11"]["x"], -2)
        self.assertFalse(compare.compare_rectangles(same, dict(same, x=13), 2)["within_tolerance"])

    def test_capture_dimensions_compared_separately_from_geometry(self):
        x11 = {"width": 300, "height": 200}
        self.assertTrue(compare.compare_capture_dimensions(301, 198, x11, tolerance=2)["within_tolerance"])
        bad = compare.compare_capture_dimensions(303, 200, x11, tolerance=2)
        self.assertFalse(bad["within_tolerance"])
        self.assertEqual(bad["delta_capture_minus_x11"], {"width": 3, "height": 0})

    @mock.patch.object(compare.subprocess, "run")
    def test_report_keeps_action_authority_false_and_saves_private_capture(self, run):
        scene = {"window": {"title": "Widget"}, "elements": [{"id": "root", "bounds": {"x": 10, "y": 20, "width": 300, "height": 200}}]}
        run.side_effect = [
            mock.Mock(stdout=(json.dumps(scene) + "\n").encode(), stderr=b""),
            mock.Mock(stdout="0x10 0 10 20 300 200 host Widget Window\n", stderr=""),
            mock.Mock(stdout=png(300, 200), stderr=b""),
        ]
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "frames" / "capture.png"
            with mock.patch("sys.argv", [str(SCRIPT), "Widget", "--save-screenshot", str(dest)]), mock.patch("sys.stdout") as out:
                status = compare.run()
                report = json.loads("".join(call.args[0] for call in out.write.call_args_list))
            self.assertEqual(status, 0)
            self.assertFalse(report["action_safe"])
            self.assertEqual(report["screen_alignment"], "unverified")
            self.assertTrue(report["comparison"]["within_tolerance"])
            self.assertTrue(report["capture_size_comparison"]["within_tolerance"])
            self.assertEqual(report["capture_size_comparison"]["delta_capture_minus_x11"], {"width": 0, "height": 0})
            self.assertEqual(dest.read_bytes(), png(300, 200))
            if os.name == "posix":  # Windows has no POSIX file modes
                self.assertEqual(dest.stat().st_mode & 0o777, 0o600)

    @mock.patch.object(compare.subprocess, "run")
    def test_duplicate_x11_matches_refuse_and_never_capture(self, run):
        scene = {"window": {"title": "Widget"}, "elements": [{"id": "root", "bounds": None}]}
        run.side_effect = [
            mock.Mock(stdout=json.dumps(scene).encode(), stderr=b""),
            mock.Mock(stdout="0x10 0 1 2 3 4 h Widget one\n0x11 0 1 2 3 4 h Widget two\n", stderr=""),
        ]
        with mock.patch("sys.argv", [str(SCRIPT), "Widget"]), mock.patch("sys.stderr") as err:
            self.assertEqual(compare.run(), 1)
        self.assertEqual(run.call_count, 2)
        self.assertIn("expected one X11 match", "".join(c.args[0] for c in err.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
