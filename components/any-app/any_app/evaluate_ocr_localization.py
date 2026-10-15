#!/usr/bin/env python3
"""Evaluate OCR word-box localization against independent labeled rectangles.

Input labels are one visible normalized word and image-local rectangle per item.
Matching is exact normalized token matching; duplicate tokens are assigned globally
by maximum IoU and threshold hits. Recognition and localization are reported
separately. This tool does not make boxes action-safe.
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate_ocr
import strict_json

# Backward-compatible public helper name used by CLI callers/tests.
load_json = strict_json.load
loads_json = strict_json.loads

MAX_DUPLICATE_TOKEN_GROUP = 64
MAX_EVALUATION_ITEMS = 10_000
MAX_ASSIGNMENT_CUBIC_WORK = 2_000_000


def _box(value, width, height, description):
    if not isinstance(value, dict):
        raise ValueError(f"{description} bounds must be an object")
    coords = [value.get(k) for k in ("x", "y", "width", "height")]
    if any(type(v) is not int for v in coords):
        raise ValueError(f"{description} bounds must use integer coordinates")
    x, y, w, h = coords
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
        raise ValueError(f"{description} bounds must be positive and inside the image")
    return (x, y, x + w, y + h)


def _iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (area_a + area_b - intersection)


def _assignment_iou_pairs(predictions, truths, threshold):
    """Return a deterministic assignment maximizing matches, hits, then IoU."""
    if not predictions or not truths:
        return []
    size = max(len(predictions), len(truths))
    cardinality = min(len(predictions), len(truths))
    # Dummy rows/columns can leave items unmatched, so encode all three
    # objectives as a strict lexicographic weighting. One threshold hit must
    # outweigh every possible IoU-sum change; one real pair must outweigh all
    # possible threshold hits and IoU from the other pairs.
    threshold_bonus = cardinality + 1.0
    match_bonus = cardinality * (threshold_bonus + 1.0) + 1.0
    weights = [[0.0] * size for _ in range(size)]
    overlaps = {}
    for i, (_, pb) in enumerate(predictions):
        for j, (_, tb) in enumerate(truths):
            overlap = _iou(pb, tb)
            overlaps[(i, j)] = overlap
            weights[i][j] = match_bonus + (threshold_bonus if overlap >= threshold else 0.0) + overlap

    # Hungarian minimum-cost assignment on a square matrix (cost = -weight).
    # Index 0 is the standard algorithm's dummy column; ordered scans make ties
    # reproducible. Padded rows/columns represent unmatched predictions/truths.
    u, v = [0.0] * (size + 1), [0.0] * (size + 1)
    p, way = [0] * (size + 1), [0] * (size + 1)
    for row in range(1, size + 1):
        p[0] = row
        minv = [float("inf")] * (size + 1)
        used = [False] * (size + 1)
        col0 = 0
        while True:
            used[col0] = True
            row0 = p[col0]
            delta, col1 = float("inf"), 0
            for col in range(1, size + 1):
                if not used[col]:
                    cost = -weights[row0 - 1][col - 1]
                    current = cost - u[row0] - v[col]
                    if current < minv[col]:
                        minv[col], way[col] = current, col0
                    if minv[col] < delta:
                        delta, col1 = minv[col], col
            for col in range(size + 1):
                if used[col]:
                    u[p[col]] += delta
                    v[col] -= delta
                else:
                    minv[col] -= delta
            col0 = col1
            if p[col0] == 0:
                break
        while True:
            col1 = way[col0]
            p[col0] = p[col1]
            col0 = col1
            if col0 == 0:
                break

    pairs = []
    for col in range(1, size + 1):
        row = p[col] - 1
        truth = col - 1
        if row >= 0 and row < len(predictions) and truth < len(truths):
            pairs.append(overlaps[(row, truth)])
    return pairs


def _optimal_iou_pairs(predictions, truths, threshold):
    """Globally optimal per-token assignment, bounded for pathological repeats.

    Unique words are constant-time to pair; only duplicate token groups need a
    Hungarian assignment. A cap prevents a contrived repeated token from
    causing cubic unbounded work in this evaluation CLI.
    """
    pred_by_token, truth_by_token = {}, {}
    for item in predictions:
        pred_by_token.setdefault(item[0], []).append(item)
    for item in truths:
        truth_by_token.setdefault(item[0], []).append(item)
    pairs = []
    assignment_work = 0
    for token, pred_group in pred_by_token.items():
        truth_group = truth_by_token.get(token, [])
        if not truth_group:
            continue
        if max(len(pred_group), len(truth_group)) > MAX_DUPLICATE_TOKEN_GROUP:
            raise ValueError(f"duplicate token group exceeds {MAX_DUPLICATE_TOKEN_GROUP} items")
        if len(pred_group) > 1 or len(truth_group) > 1:
            assignment_work += max(len(pred_group), len(truth_group)) ** 3
            if assignment_work > MAX_ASSIGNMENT_CUBIC_WORK:
                raise ValueError("duplicate-token assignment work exceeds configured limit")
        if len(pred_group) == len(truth_group) == 1:
            pairs.append(_iou(pred_group[0][1], truth_group[0][1]))
        else:
            pairs.extend(_assignment_iou_pairs(pred_group, truth_group, threshold))
    return pairs


def evaluate(ocr, labels, *, iou_threshold=0.5):
    if not isinstance(ocr, dict) or ocr.get("source") != "pixels-ocr":
        raise ValueError("OCR document must have source 'pixels-ocr'")
    if ocr.get("action_safe") is not False:
        raise ValueError("OCR document must remain action_safe=false")
    image = ocr.get("image")
    if not isinstance(image, dict) or type(image.get("width")) is not int or type(image.get("height")) is not int:
        raise ValueError("OCR image dimensions must be positive integers")
    width, height = image["width"], image["height"]
    if width <= 0 or height <= 0:
        raise ValueError("OCR image dimensions must be positive integers")
    if (not isinstance(iou_threshold, (int, float)) or isinstance(iou_threshold, bool)
            or not math.isfinite(iou_threshold) or not 0 < iou_threshold <= 1):
        raise ValueError("IoU threshold must be greater than 0 and at most 1")
    elements = ocr.get("elements")
    if not isinstance(elements, list) or not isinstance(labels, list) or not labels:
        raise ValueError("OCR elements and a non-empty labels list are required")
    if len(elements) > MAX_EVALUATION_ITEMS or len(labels) > MAX_EVALUATION_ITEMS:
        raise ValueError(f"OCR elements and labels are limited to {MAX_EVALUATION_ITEMS} items")

    predictions = []
    for item in elements:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("each OCR element must contain string text")
        tokens = evaluate_ocr.tokenize(item["text"])
        if len(tokens) != 1:
            raise ValueError("each OCR element must contain exactly one token")
        predictions.append((tokens[0], _box(item.get("bounds"), width, height, "OCR")))
    truths = []
    for item in labels:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("each label must contain one token and bounds")
        tokens = evaluate_ocr.tokenize(item["text"])
        if len(tokens) != 1:
            raise ValueError("each label must contain exactly one token")
        truths.append((tokens[0], _box(item.get("bounds"), width, height, "label")))

    pairs = _optimal_iou_pairs(predictions, truths, iou_threshold)
    recognized = len(pairs)
    loc_tp = sum(score >= iou_threshold for score in pairs)
    p_denom, r_denom = len(predictions), len(truths)
    text_precision = recognized / p_denom if p_denom else 0.0
    text_recall = recognized / r_denom if r_denom else 0.0
    loc_precision = loc_tp / p_denom if p_denom else 0.0
    loc_recall = loc_tp / r_denom if r_denom else 0.0
    mean_iou = sum(pairs) / len(pairs) if pairs else 0.0
    return {
        "metric": "exact-token-box-iou-assignment-v2", "iou_threshold": float(iou_threshold),
        "labeled_tokens": len(truths), "ocr_tokens": len(predictions),
        "text_matches": recognized,
        "text_precision": round(text_precision, 6), "text_recall": round(text_recall, 6),
        "localization_true_positive": loc_tp,
        "localization_false_positive": p_denom - loc_tp,
        "localization_false_negative": r_denom - loc_tp,
        "localization_precision": round(loc_precision, 6),
        "localization_recall": round(loc_recall, 6),
        "mean_iou_on_text_matches": round(mean_iou, 6),
        "interpretation": "Exact token matching and image-local box overlap only; no semantic or action-safety claim. Labels require independent review."
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ocr_json")
    parser.add_argument("labels_json", help="JSON array of {text,bounds:{x,y,width,height}}")
    parser.add_argument("--iou-threshold", type=float, default=0.5)
    args = parser.parse_args(argv)
    try:
        ocr = load_json(args.ocr_json)
        labels = load_json(args.labels_json)
        result = evaluate(ocr, labels, iou_threshold=args.iou_threshold)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"cannot evaluate OCR localization: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
