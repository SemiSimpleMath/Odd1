#!/usr/bin/env python3
import importlib.util
import itertools
import json
import random
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "any_app" / "evaluate_ocr_localization.py"
spec = importlib.util.spec_from_file_location("evaluate_ocr_localization", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def doc(elements, *, safe=False):
    return {"source": "pixels-ocr", "action_safe": safe,
            "image": {"width": 100, "height": 80}, "elements": elements}


def word(text, x, y, width, height):
    return {"text": text, "bounds": {"x": x, "y": y, "width": width, "height": height}}


class OcrLocalizationTests(unittest.TestCase):
    def test_json_loader_rejects_duplicate_keys_and_nonfinite_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text('{"action_safe":false,"action_safe":true}')
            with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
                module.load_json(path)
            path.write_text('{"confidence":NaN}')
            with self.assertRaisesRegex(ValueError, "non-finite JSON number"):
                module.load_json(path)
            path.write_text('{"confidence":1e9999}')
            with self.assertRaisesRegex(ValueError, "non-finite JSON number"):
                module.load_json(path)

    def test_rejects_nonfinite_thresholds(self):
        ocr = doc([word("x", 0, 0, 2, 2)])
        labels = [word("x", 0, 0, 2, 2)]
        for threshold in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(threshold=threshold):
                with self.assertRaisesRegex(ValueError, "IoU threshold"):
                    module.evaluate(ocr, labels, iou_threshold=threshold)

    def test_perfect_box_and_recognition_metrics(self):
        result = module.evaluate(doc([word("Copper", 10, 10, 20, 10)]),
                                 [word("copper", 10, 10, 20, 10)])
        self.assertEqual(result["text_precision"], 1.0)
        self.assertEqual(result["localization_precision"], 1.0)
        self.assertEqual(result["localization_recall"], 1.0)
        self.assertEqual(result["mean_iou_on_text_matches"], 1.0)

    def test_recognition_is_separate_from_bad_localization(self):
        result = module.evaluate(doc([word("Copper", 60, 60, 20, 10)]),
                                 [word("copper", 10, 10, 20, 10)])
        self.assertEqual(result["text_matches"], 1)
        self.assertEqual(result["text_recall"], 1.0)
        self.assertEqual(result["localization_true_positive"], 0)
        self.assertEqual(result["localization_false_negative"], 1)

    def test_duplicate_tokens_use_one_to_one_best_overlap_matching(self):
        result = module.evaluate(doc([word("x", 0, 0, 20, 20), word("x", 40, 0, 20, 20)]),
                                 [word("x", 0, 0, 20, 20), word("x", 40, 0, 20, 20)])
        self.assertEqual(result["localization_true_positive"], 2)
        self.assertEqual(result["mean_iou_on_text_matches"], 1.0)

    def test_assignment_maximizes_threshold_hits_not_greedy_overlap(self):
        # Greedy picks prediction 1 -> label 1 (IoU .647), leaving only one
        # threshold hit. The global assignment gets p1 -> label 2 (.514) and
        # p2 -> label 1 (.629), for two valid localization matches.
        result = module.evaluate(
            doc([word("x", 28, 0, 26, 20), word("x", 15, 0, 27, 20)]),
            [word("x", 20, 0, 30, 20), word("x", 35, 0, 30, 20)])
        self.assertEqual(result["text_matches"], 2)
        self.assertEqual(result["localization_true_positive"], 2)
        self.assertEqual(result["localization_false_negative"], 0)
        self.assertEqual(result["mean_iou_on_text_matches"], 0.571042)

    def test_rectangular_assignment_matches_exhaustive_objective(self):
        # Exercise both padded-row and padded-column cases. The optimizer must
        # maximize threshold hits first and total IoU second for every injective
        # pairing of the smaller group, not only for square duplicate groups.
        rng = random.Random(20261013)
        for prediction_count in range(1, 5):
            for label_count in range(1, 5):
                predictions, labels = [], []
                for target, count in ((predictions, prediction_count), (labels, label_count)):
                    for _ in range(count):
                        x, y = rng.randrange(0, 60), rng.randrange(0, 40)
                        width, height = rng.randrange(10, 31), rng.randrange(10, 31)
                        target.append(("x", (x, y, x + width, y + height)))
                actual = module._assignment_iou_pairs(predictions, labels, 0.5)
                cardinality = min(prediction_count, label_count)
                self.assertEqual(len(actual), cardinality)
                if prediction_count <= label_count:
                    candidates = [
                        [module._iou(predictions[i][1], labels[j][1]) for i, j in enumerate(perm)]
                        for perm in itertools.permutations(range(label_count), prediction_count)
                    ]
                else:
                    candidates = [
                        [module._iou(predictions[i][1], labels[j][1]) for i, j in zip(indices, perm)]
                        for indices in itertools.combinations(range(prediction_count), label_count)
                        for perm in itertools.permutations(range(label_count))
                    ]
                expected = max((sum(value >= 0.5 for value in scores), sum(scores))
                               for scores in candidates)
                self.assertEqual(sum(value >= 0.5 for value in actual), expected[0])
                self.assertAlmostEqual(sum(actual), expected[1], places=12)

    def test_nonmatching_text_is_not_given_a_spatial_pair(self):
        result = module.evaluate(doc([word("wrong", 0, 0, 20, 20)]),
                                 [word("right", 0, 0, 20, 20)])
        self.assertEqual(result["text_matches"], 0)
        self.assertEqual(result["localization_true_positive"], 0)
        self.assertEqual(result["localization_false_positive"], 1)
        self.assertEqual(result["localization_false_negative"], 1)

    def test_repeated_token_assignment_has_a_reasonable_work_cap(self):
        predictions = [word("repeat", i, 0, 1, 1) for i in range(65)]
        labels = [word("repeat", i, 0, 1, 1) for i in range(65)]
        with self.assertRaisesRegex(ValueError, "duplicate token group exceeds"):
            module.evaluate(doc(predictions), labels)

    def test_aggregate_assignment_work_is_bounded(self):
        predictions, labels = [], []
        for group in range(32):
            token = f"repeat{group}"
            for i in range(40):
                x = i % 80
                predictions.append(word(token, x, 0, 1, 1))
                labels.append(word(token, x, 1, 1, 1))
        with self.assertRaisesRegex(ValueError, "assignment work exceeds"):
            module.evaluate(doc(predictions), labels)

    def test_item_count_is_bounded(self):
        predictions = [word("x", 0, 0, 1, 1)] * 10001
        with self.assertRaisesRegex(ValueError, "limited to"):
            module.evaluate(doc(predictions), [word("x", 0, 0, 1, 1)])

    def test_rejects_out_of_image_boxes_and_split_tokens(self):
        with self.assertRaisesRegex(ValueError, "inside the image"):
            module.evaluate(doc([word("x", 99, 0, 2, 2)]), [word("x", 0, 0, 2, 2)])
        with self.assertRaisesRegex(ValueError, "exactly one token"):
            module.evaluate(doc([word("two words", 0, 0, 20, 10)]), [word("two", 0, 0, 5, 10)])

    def test_rejects_action_authority_and_bad_threshold(self):
        with self.assertRaisesRegex(ValueError, "action_safe=false"):
            module.evaluate(doc([word("x", 0, 0, 2, 2)], safe=True), [word("x", 0, 0, 2, 2)])
        with self.assertRaisesRegex(ValueError, "IoU threshold"):
            module.evaluate(doc([word("x", 0, 0, 2, 2)]), [word("x", 0, 0, 2, 2)], iou_threshold=0)


if __name__ == "__main__":
    unittest.main()
