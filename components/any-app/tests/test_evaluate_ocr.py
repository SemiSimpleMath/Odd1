#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "any_app" / "evaluate_ocr.py"
spec = importlib.util.spec_from_file_location("evaluate_ocr", SCRIPT)
evaluate_ocr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluate_ocr)


def document(words):
    return {"source": "pixels-ocr", "action_safe": False,
            "elements": [{"text": word} for word in words]}


class EvaluateOcrTests(unittest.TestCase):
    def test_exact_normalized_token_counts(self):
        result = evaluate_ocr.evaluate(document(["File", "Icopper", "compass", "zebra", "8142"]),
                                       ["Copper compass zebra 8142"])
        self.assertEqual((result["true_positive"], result["false_positive"],
                          result["false_negative"]), (3, 2, 1))
        self.assertEqual(result["precision"], 0.6)
        self.assertEqual(result["recall"], 0.75)
        self.assertEqual(result["f1"], 0.666667)
        self.assertCountEqual(result["false_negative_tokens"], ["copper"])

    def test_punctuation_case_and_duplicates_normalize(self):
        result = evaluate_ocr.evaluate(document(["COPPER,", "copper", "42!"]),
                                       ["copper", "42"])
        self.assertEqual((result["true_positive"], result["false_positive"],
                          result["false_negative"]), (2, 1, 0))

    def test_requires_non_authorizing_pixel_ocr(self):
        with self.assertRaisesRegex(ValueError, "action_safe=false"):
            evaluate_ocr.evaluate({**document(["word"]), "action_safe": True}, ["word"])
        with self.assertRaisesRegex(ValueError, "pixels-ocr"):
            evaluate_ocr.evaluate({"source": "at-spi", "action_safe": False,
                                   "elements": []}, ["word"])

    def test_strict_json_load_rejects_duplicate_and_nonfinite_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for text, message in (
                ('{"action_safe":false,"action_safe":true}', "duplicate JSON object key"),
                ('{"confidence":NaN}', "non-finite JSON number"),
                ('{"confidence":1e9999}', "non-finite JSON number"),
            ):
                path.write_text(text)
                with self.subTest(text=text), self.assertRaisesRegex(ValueError, message):
                    evaluate_ocr.strict_json.load(path)

    def test_strict_json_file_read_is_bounded_before_decode(self):
        strict = evaluate_ocr.strict_json
        original_limit = strict.MAX_JSON_BYTES
        try:
            strict.MAX_JSON_BYTES = 16
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "oversized.json"
                path.write_bytes(b"{}" + b" " * 40)
                with self.assertRaisesRegex(ValueError, "exceeds 16 bytes"):
                    strict.load(path)
        finally:
            strict.MAX_JSON_BYTES = original_limit

    def test_strict_json_reader_requests_only_limit_plus_one_bytes(self):
        strict = evaluate_ocr.strict_json
        original_limit = strict.MAX_JSON_BYTES
        try:
            strict.MAX_JSON_BYTES = 16
            stream = mock.mock_open(read_data=b"{}" + b" " * 40)
            with mock.patch.object(Path, "open", stream):
                with self.assertRaisesRegex(ValueError, "exceeds 16 bytes"):
                    strict.load("oversized.json")
            stream.return_value.read.assert_called_once_with(17)
        finally:
            strict.MAX_JSON_BYTES = original_limit

    def test_strict_json_text_limit_counts_utf8_bytes(self):
        strict = evaluate_ocr.strict_json
        original_limit = strict.MAX_JSON_BYTES
        try:
            strict.MAX_JSON_BYTES = 8
            with self.assertRaisesRegex(ValueError, "exceeds 8 bytes"):
                strict.loads('{"x":"éé"}')
        finally:
            strict.MAX_JSON_BYTES = original_limit

    def test_requires_labeled_tokens(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            evaluate_ocr.evaluate(document(["word"]), [])
        with self.assertRaisesRegex(ValueError, "no tokens"):
            evaluate_ocr.evaluate(document(["word"]), ["!!!"])


if __name__ == "__main__":
    unittest.main()
