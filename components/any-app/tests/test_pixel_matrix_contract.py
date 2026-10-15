import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parent / "pixel_matrix_contract.py"
spec = importlib.util.spec_from_file_location("pixel_matrix_contract", MODULE)
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class PixelMatrixContractTests(unittest.TestCase):
    def test_known_text_requires_expected_tokens_and_reports_false_positives(self):
        result = contract.evaluate_oracle(["violet", "lighthouse", "482", "noise"],
                                          ["violet", "lighthouse", "482"], "tokens")
        self.assertTrue(result["passed"])
        self.assertEqual(result["missing_tokens"], [])
        self.assertEqual(result["unexpected_tokens"], ["noise"])
        self.assertEqual(result["observed_tokens"], ["482", "lighthouse", "noise", "violet"])
        self.assertEqual(result["ocr_word_count"], 4)

    def test_known_text_reports_misses(self):
        result = contract.evaluate_oracle(["violet", "482"], ["violet", "lighthouse", "482"], "tokens")
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_tokens"], ["lighthouse"])

    def test_empty_oracle_rejects_any_ocr_words(self):
        self.assertFalse(contract.evaluate_oracle(["ghost"], [], "empty")["passed"])
        self.assertTrue(contract.evaluate_oracle([], [], "empty")["passed"])

    def test_diagnostic_oracle_records_without_asserting_quality(self):
        result = contract.evaluate_oracle(["muted", "wrong"], ["muted", "signal"], "diagnostic")
        self.assertTrue(result["passed"])
        self.assertEqual(result["missing_tokens"], ["signal"])
        self.assertEqual(result["unexpected_tokens"], ["wrong"])

    def test_diagnostic_output_is_bounded_for_large_token_set(self):
        result = contract.evaluate_oracle([f"token{i}" for i in range(100)], [], "diagnostic")
        self.assertEqual(result["observed_token_count"], 100)
        self.assertEqual(len(result["observed_tokens"]), 50)
        self.assertTrue(result["observed_tokens_truncated"])
        self.assertEqual(result["unexpected_token_count"], 100)
        self.assertTrue(result["unexpected_tokens_truncated"])

    def test_expected_token_limits_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "bounds"):
            contract.evaluate_oracle([], ["x" * 161], "tokens")
        with self.assertRaisesRegex(ValueError, "bounds"):
            contract.evaluate_oracle([], [f"t{i}" for i in range(65)], "tokens")

    def test_unknown_oracle_rejected(self):
        with self.assertRaises(ValueError):
            contract.evaluate_oracle([], [], "guess")


if __name__ == "__main__":
    unittest.main()
