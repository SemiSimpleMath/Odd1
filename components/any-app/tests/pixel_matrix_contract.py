"""Pure oracle contract for the live synthetic OCR matrix."""


def evaluate_oracle(words, expected_tokens, oracle):
    """Return token diagnostics; diagnostic fixtures never assert OCR quality."""
    present = set(words)
    expected = set(expected_tokens)
    if len(expected) > 64 or any(len(token) > 160 for token in expected):
        raise ValueError("expected token set exceeds diagnostic bounds")
    missing = sorted(expected - present)
    if oracle == "tokens":
        passed = not missing
    elif oracle == "empty":
        passed = not words
    elif oracle == "diagnostic":
        passed = True
    else:
        raise ValueError(f"unknown oracle mode: {oracle}")
    unexpected = sorted(present - expected)
    # Extra OCR tokens are not adjudicated: window decorations may contain
    # readable text. Keep diagnostics bounded even on pathological OCR output.
    cap = 50
    return {"passed": passed, "missing_tokens": missing,
            "unexpected_tokens": unexpected[:cap],
            "unexpected_token_count": len(unexpected),
            "unexpected_tokens_truncated": len(unexpected) > cap,
            "observed_tokens": sorted(present)[:cap],
            "observed_token_count": len(present),
            "observed_tokens_truncated": len(present) > cap,
            "ocr_word_count": len(words)}
