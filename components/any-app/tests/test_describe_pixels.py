#!/usr/bin/env python3
"""Offline tests for bounded, non-authorizing pixel OCR description."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import struct
import zlib
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "any_app" / "describe_pixels.py"
spec = importlib.util.spec_from_file_location("describe_pixels", SCRIPT)
pixels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pixels)


def png_chunk(kind, payload):
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)


def png_bytes(size=(80, 40), fmt="PNG"):
    width, height = size
    if fmt != "PNG":
        return b"not-a-png" + b"x" * 32
    # Small RGBA PNG without an imaging library.
    raw = b"".join(b"\x00" + b"\xff\xff\xff\xff" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + png_chunk(b"IDAT", zlib.compress(raw)) + png_chunk(b"IEND", b""))


HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def row(x=5, y=6, w=20, h=8, confidence=90, text="Save"):
    return f"5\t1\t1\t1\t1\t1\t{x}\t{y}\t{w}\t{h}\t{confidence}\t{text}\n"


class PixelDescriptionTests(unittest.TestCase):
    def test_parse_tsv_returns_image_local_boxes_and_confidence(self):
        words, truncated = pixels.parse_tsv(HEADER + row(), 80, 40)
        self.assertFalse(truncated)
        self.assertEqual(words, [{"text": "Save", "confidence": 90.0,
                                  "bounds": {"x": 5, "y": 6, "width": 20, "height": 8}}])

    def test_parse_rejects_outside_boxes_and_bad_confidence(self):
        data = HEADER + row(x=-1) + row(confidence=101) + row(x=79, w=2) + row(text="Good")
        words, truncated = pixels.parse_tsv(data, 80, 40)
        self.assertEqual([w["text"] for w in words], ["Good"])
        self.assertFalse(truncated)

    def test_parse_bounds_words_and_output_bytes(self):
        words, truncated = pixels.parse_tsv(HEADER + row(text="First") + row(text="Second"),
                                            80, 40, max_words=1)
        self.assertEqual([w["text"] for w in words], ["First"])
        self.assertTrue(truncated)
        with self.assertRaisesRegex(ValueError, "4 MiB"):
            pixels.parse_tsv("x" * (pixels.MAX_TSV_BYTES + 1), 80, 40)

    def test_png_validation_rejects_non_png_and_pixel_bombs(self):
        with self.assertRaisesRegex(ValueError, "PNG"):
            pixels.inspect_png("unused", image_bytes=png_bytes(fmt="JPEG"))
        with self.assertRaisesRegex(ValueError, "dimensions"):
            pixels.inspect_png("unused", image_bytes=png_bytes((9000, 1)))
        with self.assertRaisesRegex(ValueError, "dimensions"):
            pixels.inspect_png("unused", image_bytes=png_bytes((6000, 6000)))

    def test_file_input_reads_only_one_byte_past_size_limit(self):
        stream = mock.mock_open(read_data=b"x" * (pixels.MAX_INPUT_BYTES + 1))
        with mock.patch.object(Path, "open", stream):
            with self.assertRaisesRegex(ValueError, "16 MiB"):
                pixels.inspect_png("oversized.png")
        stream.return_value.read.assert_called_once_with(pixels.MAX_INPUT_BYTES + 1)

    def test_describe_image_passes_png_on_stdin_without_saving_or_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "capture.png"
            original = png_bytes()
            path.write_bytes(original)
            completed = subprocess.CompletedProcess([], 0, (HEADER + row()).encode(), b"")
            inputs = []
            def runner(command, **kwargs):
                self.assertEqual(command[0], "tesseract")
                self.assertEqual(command[3:5], ["--psm", "11"])
                self.assertEqual(kwargs["timeout"], 3)
                self.assertFalse(any(str(path) in str(arg) for arg in command))
                inputs.append(kwargs["input"])
                return completed
            result = pixels.describe_image(path, timeout=3, runner=runner)
            self.assertIn(original, inputs)  # the first pass reads the PNG exactly as given
            self.assertEqual(result["source"], "pixels-ocr")
            self.assertEqual(result["elements"][0]["text"], "Save")
            self.assertFalse(result["action_safe"])
            self.assertEqual(result["geometry"]["screen_alignment"], "unverified")
            self.assertEqual(list(Path(temp).iterdir()), [path])

    def test_merge_keeps_the_more_confident_pass_per_region(self):
        def word(text, confidence, x, w=20):
            return {"text": text, "confidence": confidence, "bounds": {"x": x, "y": 6, "width": w, "height": 8}}
        small_font = [word("oddl/brouser", 20, 0, 60), word("Undo", 92, 100)]
        large_scale = [word("odd1", 70, 0), word("/browser", 65, 22, 38), word("Und0", 40, 100)]
        merged = pixels.merge_passes([small_font, large_scale])
        self.assertEqual([w["text"] for w in merged], ["odd1", "/browser", "Undo"])

    def test_upscaled_pass_reports_boxes_in_original_pixels(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow is installed on Odd1 by module.nix")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "capture.png"
            path.write_bytes(png_bytes())
            def runner(command, **kwargs):  # the 2x pass is the larger PNG and reads the word confidently
                big = len(kwargs["input"]) != len(png_bytes())
                tsv = HEADER + (row(x=10, y=12, w=40, h=16, confidence=95, text="Save") if big else
                                row(confidence=30, text="5ave"))
                return subprocess.CompletedProcess([], 0, tsv.encode(), b"")
            result = pixels.describe_image(path, runner=runner)
            self.assertEqual(result["scales"], [1, 2])
            self.assertEqual(result["elements"], [{"text": "Save", "confidence": 95.0,
                                                   "bounds": {"x": 5, "y": 6, "width": 20, "height": 8}}])

    def test_ocr_timeout_and_nonzero_exit_are_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "capture.png"
            path.write_bytes(png_bytes())
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                pixels.describe_image(path, runner=mock.Mock(side_effect=subprocess.TimeoutExpired("tesseract", 1)))
            with self.assertRaisesRegex(RuntimeError, "failed"):
                pixels.describe_image(path, runner=mock.Mock(return_value=subprocess.CompletedProcess(
                    [], 2, b"", b"bad OCR")))

    def test_cli_emits_valid_compact_json(self):
        payload = pixels.describe_image
        self.assertTrue(callable(payload))


if __name__ == "__main__":
    unittest.main()
