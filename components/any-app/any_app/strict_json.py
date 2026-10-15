#!/usr/bin/env python3
"""Small strict, bounded JSON reader for untrusted project/evidence artifacts.

Rejects duplicate object keys, non-finite numbers (including exponent overflow),
and inputs larger than MAX_JSON_BYTES. JSON serialization is intentionally left
to callers.
"""
import json
import math

import bounded_io

MAX_JSON_BYTES = 8 * 1024 * 1024


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value):
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number is not allowed: {value}")
    return parsed


def loads(text):
    """Parse JSON text, rejecting oversized, ambiguous, or non-finite input."""
    if not isinstance(text, str):
        raise TypeError("JSON input must be text")
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError(f"JSON input exceeds {MAX_JSON_BYTES} bytes")
    return json.loads(text, object_pairs_hook=_reject_duplicate_keys,
                      parse_constant=_reject_nonfinite, parse_float=_finite_float)


def load(path):
    """Read at most MAX_JSON_BYTES + 1 bytes from UTF-8 JSON at a path."""
    data = bounded_io.read_bytes(path, MAX_JSON_BYTES, "JSON input")
    return loads(data.decode("utf-8"))
