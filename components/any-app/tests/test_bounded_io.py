#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "any_app" / "bounded_io.py"
spec = importlib.util.spec_from_file_location("bounded_io_under_test", SCRIPT)
bounded_io = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bounded_io)


class BoundedIoTests(unittest.TestCase):
    def test_exact_limit_succeeds_and_one_byte_over_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            path.write_bytes(b"1234")
            self.assertEqual(bounded_io.read_bytes(path, 4), b"1234")
            path.write_bytes(b"12345")
            with self.assertRaisesRegex(ValueError, "exceeds 4 bytes"):
                bounded_io.read_bytes(path, 4)

    def test_only_limit_plus_one_bytes_are_requested(self):
        stream = mock.mock_open(read_data=b"x" * 100)
        with mock.patch.object(Path, "open", stream):
            with self.assertRaisesRegex(ValueError, "exceeds 7 bytes"):
                bounded_io.read_bytes("input", 7, "test input")
        stream.return_value.read.assert_called_once_with(8)

    def test_streamed_hash_enforces_actual_byte_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact"
            path.write_bytes(b"abcdef")
            digest, count = bounded_io.sha256_file(path, 6)
            self.assertEqual(count, 6)
            self.assertEqual(digest, __import__("hashlib").sha256(b"abcdef").hexdigest())
            with self.assertRaisesRegex(ValueError, "exceeds 5 bytes"):
                bounded_io.sha256_file(path, 5, "test artifact")

    def test_streamed_hash_requests_no_more_than_remaining_plus_sentinel(self):
        stream = mock.mock_open(read_data=b"x" * 100)
        with mock.patch.object(Path, "open", stream):
            with self.assertRaisesRegex(ValueError, "exceeds 7 bytes"):
                bounded_io.sha256_file("input", 7, "test input")
        reads = stream.return_value.read.call_args_list
        self.assertEqual(reads[0].args[0], 8)
        self.assertEqual(len(reads), 1)

    def test_bounded_text_is_strict_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "utf8"
            path.write_bytes("é".encode("utf-8"))
            self.assertEqual(bounded_io.read_text(path, 2), "é")
            path.write_bytes(bytes([255]))
            with self.assertRaises(UnicodeDecodeError):
                bounded_io.read_text(path, 1)

    def test_limit_argument_rejects_bool_and_negative(self):
        for limit in (True, -1, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                bounded_io.read_bytes("unused", limit)


if __name__ == "__main__":
    unittest.main()
