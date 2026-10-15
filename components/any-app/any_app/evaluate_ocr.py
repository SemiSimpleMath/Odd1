#!/usr/bin/env python3
"""Compare pixel OCR tokens with a manually labeled word list.

This reports exact token-level micro metrics, not semantic accuracy. Matching is
case-insensitive and strips punctuation; duplicate tokens are counted. OCR input
must be output from describe_pixels.py and remains untrusted/non-authorizing.
"""
import argparse
from collections import Counter
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strict_json

TOKEN = re.compile(r"[^\w]+", re.UNICODE)


def tokenize(text):
    if not isinstance(text, str):
        raise ValueError("tokens must be strings")
    return [part for part in TOKEN.sub(" ", text.casefold()).split() if part]


def evaluate(ocr, expected):
    if not isinstance(ocr, dict) or ocr.get("source") != "pixels-ocr":
        raise ValueError("OCR document must have source 'pixels-ocr'")
    if ocr.get("action_safe") is not False:
        raise ValueError("OCR document must remain action_safe=false")
    elements = ocr.get("elements")
    if not isinstance(elements, list):
        raise ValueError("OCR elements must be a list")
    if not isinstance(expected, list) or not expected:
        raise ValueError("expected must be a non-empty list of labeled strings")
    emitted = []
    for element in elements:
        if not isinstance(element, dict) or not isinstance(element.get("text"), str):
            raise ValueError("each OCR element must contain string text")
        emitted.extend(tokenize(element["text"]))
    labels = []
    for text in expected:
        labels.extend(tokenize(text))
    if not labels:
        raise ValueError("expected labels contain no tokens")
    remaining = Counter(labels)
    true_positive = 0
    false_positive = []
    for token in emitted:
        if remaining[token]:
            remaining[token] -= 1
            true_positive += 1
        else:
            false_positive.append(token)
    false_negative = list(remaining.elements())
    denominator_p = true_positive + len(false_positive)
    denominator_r = true_positive + len(false_negative)
    precision = true_positive / denominator_p if denominator_p else 0.0
    recall = true_positive / denominator_r if denominator_r else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"metric": "exact-normalized-token-micro-v1", "labeled_tokens": len(labels),
            "ocr_tokens": len(emitted), "true_positive": true_positive,
            "false_positive": len(false_positive), "false_negative": len(false_negative),
            "precision": round(precision, 6), "recall": round(recall, 6),
            "f1": round(f1, 6), "false_positive_tokens": false_positive,
            "false_negative_tokens": false_negative,
            "interpretation": "Exact token overlap only; no semantic, spatial, or action-safety claim."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ocr_json", help="describe_pixels.py JSON output")
    parser.add_argument("labels_json", help="JSON array of manually labeled visible text strings")
    args = parser.parse_args(argv)
    try:
        ocr = strict_json.load(args.ocr_json)
        labels = strict_json.load(args.labels_json)
        result = evaluate(ocr, labels)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"cannot evaluate OCR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
