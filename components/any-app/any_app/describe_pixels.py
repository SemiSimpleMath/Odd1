#!/usr/bin/env python3
"""Describe visible text in a PNG using bounded local Tesseract OCR.

This is a pixel-only observation aid: word boxes are image-local, uncertain OCR
output, never screen coordinates or authorization to act. Requires Pillow and the
`tesseract` executable; no image or text is sent to a remote service.
"""
import argparse
import concurrent.futures
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_WIDTH = 8192
MAX_HEIGHT = 8192
MAX_PIXELS = 32_000_000
MAX_TSV_BYTES = 4 * 1024 * 1024
MAX_WORDS = 10_000


def inspect_png(path, *, image_bytes=None, image_module=None):
    """Validate a bounded PNG and return dimensions and exact source bytes."""
    target = Path(path)
    if image_bytes is None:
        # Do not let an unexpectedly large screenshot consume unbounded memory
        # before the documented input-size check. One sentinel byte proves the
        # limit was exceeded without reading the rest of the file.
        with target.open("rb") as stream:
            data = stream.read(MAX_INPUT_BYTES + 1)
    else:
        data = image_bytes
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("PNG input exceeds 16 MiB")
    # Validate PNG structure and every chunk CRC using only the standard library.
    import struct
    import zlib
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("input must be a PNG")
    offset, width, height = 8, None, None
    saw_idat = saw_iend = False
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError("invalid PNG: truncated chunk")
        length = struct.unpack(">I", data[offset:offset+4])[0]
        kind = data[offset+4:offset+8]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("invalid PNG: truncated payload")
        payload = data[offset+8:offset+8+length]
        expected = struct.unpack(">I", data[offset+8+length:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != expected:
            raise ValueError("invalid PNG: CRC mismatch")
        if width is None:
            if kind != b"IHDR" or length != 13:
                raise ValueError("invalid PNG: missing IHDR")
            width, height = struct.unpack(">II", payload[:8])
        if kind == b"IDAT": saw_idat = True
        if kind == b"IEND":
            if length != 0 or end != len(data):
                raise ValueError("invalid PNG: bad IEND/trailing bytes")
            saw_iend = True
            break
        offset = end
    if width is None or not saw_idat or not saw_iend:
        raise ValueError("invalid PNG: incomplete image")
    if (width <= 0 or height <= 0 or width > MAX_WIDTH or
            height > MAX_HEIGHT or width * height > MAX_PIXELS):
        raise ValueError("PNG dimensions exceed configured limits")
    return data, width, height


def parse_tsv(tsv, width, height, *, max_words=MAX_WORDS):
    """Parse Tesseract TSV level-5 rows; retain only plausible in-image boxes."""
    if isinstance(tsv, bytes):
        if len(tsv) > MAX_TSV_BYTES:
            raise ValueError("OCR output exceeds 4 MiB")
        tsv = tsv.decode("utf-8", "replace")
    elif len(tsv.encode("utf-8")) > MAX_TSV_BYTES:
        raise ValueError("OCR output exceeds 4 MiB")
    rows = []
    truncated = False
    lines = tsv.splitlines()
    if not lines or not lines[0].startswith("level\tpage_num\t"):
        raise ValueError("malformed Tesseract TSV header")
    for line in lines[1:]:
        fields = line.split("\t", 11)
        if len(fields) != 12:
            continue
        try:
            level = int(fields[0])
            x, y, w, h = map(int, fields[6:10])
            confidence = float(fields[10])
        except (TypeError, ValueError):
            continue
        text = " ".join(fields[11].split())
        if level != 5 or not text or w <= 0 or h <= 0:
            continue
        if x < 0 or y < 0 or x + w > width or y + h > height:
            continue
        if not (-1 <= confidence <= 100):
            continue
        if len(rows) >= max_words:
            truncated = True
            break
        rows.append({"text": text[:160], "confidence": round(confidence, 1),
                     "bounds": {"x": x, "y": y, "width": w, "height": h}})
    return rows, truncated


# One OpenMP thread per Tesseract: passes run in parallel, and Tesseract's own threads then oversubscribe the CPUs
# (a 2x Firefox window went from 1 s to a 10 s timeout).
TESSERACT_ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}


def run_tesseract(data, *, timeout, runner):
    """One Tesseract pass over PNG bytes on stdin; returns its TSV bytes."""
    try:
        result = runner(["tesseract", "stdin", "stdout", "--psm", "11", "tsv"],
                        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        timeout=timeout, check=False, env=TESSERACT_ENV)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Tesseract OCR timed out") from exc
    except OSError as exc:
        raise RuntimeError(f"cannot start local Tesseract: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Tesseract failed ({result.returncode}): {detail}")
    if len(result.stdout) > MAX_TSV_BYTES:
        raise ValueError("OCR output exceeds 4 MiB")
    return result.stdout


def upscaled(data, scale):
    """The image in grayscale, `scale` times larger (Lanczos), as PNG bytes; None without Pillow or when the
    result would exceed the pixel limit. Small fonts (xterm, toolbars, status bars) read far better this way."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    image = Image.open(io.BytesIO(data))
    if image.width * image.height * scale * scale > MAX_PIXELS:
        return None
    gray = ImageOps.grayscale(image.convert("RGB"))
    out = io.BytesIO()
    gray.resize((image.width * scale, image.height * scale), Image.LANCZOS).save(out, "PNG")
    return out.getvalue()


def overlaps(a, b, share=0.3):
    """True when two word boxes overlap by at least `share` of the smaller one."""
    ix = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
    iy = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
    if ix <= 0 or iy <= 0:
        return False
    return ix * iy >= share * min(a["width"] * a["height"], b["width"] * b["height"])


def merge_passes(passes):
    """Merge word lists read from the same image at different scales: overlapping words form a region, and each
    region keeps the words of the pass that read it with the highest mean confidence. Reading order is kept."""
    words = [(index, word) for index, found in enumerate(passes) for word in found]
    parent = list(range(len(words)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (pass_i, a) in enumerate(words):
        for j in range(i + 1, len(words)):
            pass_j, b = words[j]
            if pass_i != pass_j and overlaps(a["bounds"], b["bounds"]):
                parent[root(i)] = root(j)
    regions = {}
    for i, (index, word) in enumerate(words):
        regions.setdefault(root(i), {}).setdefault(index, []).append(word)
    kept = []
    for by_pass in regions.values():
        best = max(by_pass.values(), key=lambda found: sum(w["confidence"] for w in found) / len(found))
        kept.extend(best)
    return sorted(kept, key=lambda w: (w["bounds"]["y"] + w["bounds"]["height"] // 2, w["bounds"]["x"]))


def describe_image(path, *, max_words=MAX_WORDS, timeout=10, runner=subprocess.run, scales=(1, 2)):
    """OCR a PNG once per scale (1 = the PNG as given; n = grayscale, n times larger), in parallel, and merge."""
    data, width, height = inspect_png(path)
    inputs = [(1, data) if scale == 1 else (scale, upscaled(data, scale)) for scale in scales]
    inputs = [(scale, image) for scale, image in inputs if image is not None]
    with concurrent.futures.ThreadPoolExecutor(len(inputs)) as pool:
        raws = list(pool.map(lambda item: run_tesseract(item[1], timeout=timeout, runner=runner), inputs))
    passes, truncated = [], False
    for (scale, _), raw in zip(inputs, raws):
        found, cut = parse_tsv(raw, width * scale, height * scale, max_words=max_words)
        truncated |= cut
        for word in found:
            word["bounds"] = {key: max(value // scale, 1 if key in ("width", "height") else 0)
                              for key, value in word["bounds"].items()}
        passes.append(found)
    words = merge_passes(passes) if len(passes) > 1 else passes[0]
    if len(words) > max_words:
        words, truncated = words[:max_words], True
    return {"source": "pixels-ocr", "image": {"sha256": hashlib.sha256(data).hexdigest(),
            "width": width, "height": height},
            "elements": words, "truncated": truncated,
            "counts": {"emitted_elements": len(words)}, "scales": [scale for scale, _ in inputs],
            "geometry": {"coordinate_space": "image-local-pixels",
                         "screen_alignment": "unverified"},
            "action_safe": False,
            "note": "OCR text and boxes are uncertain visual observations tied to this PNG; not screen coordinates or action authorization."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("png", help="local PNG screenshot")
    parser.add_argument("--max-words", type=int, default=MAX_WORDS)
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args(argv)
    if not 1 <= args.max_words <= MAX_WORDS:
        parser.error("--max-words must be between 1 and 10000")
    if not 0 < args.timeout <= 60:
        parser.error("--timeout must be greater than 0 and at most 60 seconds")
    try:
        result = describe_image(args.png, max_words=args.max_words, timeout=args.timeout)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"cannot describe PNG: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
