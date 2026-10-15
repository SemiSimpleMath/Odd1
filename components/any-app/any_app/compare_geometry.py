#!/usr/bin/env python3
"""Compare one AT-SPI window's reported geometry to independent X11 geometry.

This is an evidence collector, not a calibrator: it never promotes bounds for
input actions. Requires wmctrl, ImageMagick ``import``, pyatspi and a live X11
session. Screenshot pixels are captured to memory and represented only by size
and SHA-256 unless the caller explicitly asks to save them.
"""
import argparse
import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
from pathlib import Path

MAX_DESCRIPTOR_BYTES = 1_048_576


def parse_wmctrl(text):
    """Parse `wmctrl -l -G` lines into X11 window records."""
    result = []
    for line in text.splitlines():
        fields = line.split(None, 7)
        if len(fields) != 8:
            continue
        try:
            result.append({"id": fields[0], "desktop": int(fields[1]),
                           "x": int(fields[2]), "y": int(fields[3]),
                           "width": int(fields[4]), "height": int(fields[5]),
                           "host": fields[6], "title": fields[7]})
        except ValueError:
            continue
    return result


def png_dimensions(data):
    """Read PNG IHDR dimensions, rejecting non-PNG or malformed captures."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("X11 screenshot is not a valid PNG")
    width, height = struct.unpack(">II", data[16:24])
    if not width or not height:
        raise ValueError("PNG has zero dimensions")
    return width, height


def compare_rectangles(atspi, x11, tolerance=2):
    """Return deltas and a strict all-fields-within-tolerance comparison."""
    keys = ("x", "y", "width", "height")
    delta = {key: atspi[key] - x11[key] for key in keys}
    return {"delta_atspi_minus_x11": delta,
            "within_tolerance": all(abs(value) <= tolerance for value in delta.values())}


def compare_capture_dimensions(width, height, x11, tolerance=2):
    """Compare captured PNG size with X11-reported window size; evidence only."""
    delta = {"width": width - x11["width"], "height": height - x11["height"]}
    return {"delta_capture_minus_x11": delta,
            "within_tolerance": all(abs(value) <= tolerance for value in delta.values())}


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("window", help="unique accessible window title substring")
    parser.add_argument("--window-id", help="explicit X11 window ID; otherwise uniquely match title")
    parser.add_argument("--tolerance", type=int, default=2, help="comparison tolerance in pixels (default 2)")
    parser.add_argument("--save-screenshot", help="optional PNG output path (created mode 0600)")
    args = parser.parse_args(argv)
    if args.tolerance < 0 or args.tolerance > 100:
        parser.error("--tolerance must be between 0 and 100")

    try:
        descriptor = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("describe_accessible.py")),
             args.window, "--max-bytes", str(MAX_DESCRIPTOR_BYTES)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        if len(descriptor.stdout) > MAX_DESCRIPTOR_BYTES:
            raise ValueError("AT-SPI descriptor exceeded its declared output bound")
        scene = json.loads(descriptor.stdout)
        records = parse_wmctrl(subprocess.run(
            ["wmctrl", "-l", "-G"], check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=5).stdout)
        title = scene["window"]["title"]
        if args.window_id:
            matches = [record for record in records if record["id"].lower() == args.window_id.lower()]
        else:
            matches = [record for record in records if title.casefold() in record["title"].casefold()]
        if len(matches) != 1:
            raise ValueError(f"expected one X11 match for {title!r}, got {len(matches)}; use --window-id")
        x11 = matches[0]
        image = subprocess.run(["import", "-window", x11["id"], "png:-"],
                               check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10).stdout
        width, height = png_dimensions(image)
        if args.save_screenshot:
            target = Path(args.save_screenshot)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(target.parent, stat.S_IMODE(target.parent.stat().st_mode) & 0o700)
            target.write_bytes(image)
            os.chmod(target, 0o600)

        frame = next((item for item in scene["elements"] if item["id"] == "root"), None)
        atspi = frame.get("bounds") if frame else None
        comparison = compare_rectangles(atspi, x11, args.tolerance) if atspi else None
        capture_comparison = compare_capture_dimensions(width, height, x11, args.tolerance)
        report = {"source": "at-spi+x11",
                  "window": scene["window"], "x11_window": x11,
                  "atspi_frame_bounds": atspi,
                  "screenshot": {"format": "png", "width": width, "height": height,
                                 "sha256": hashlib.sha256(image).hexdigest(),
                                 "saved_to": args.save_screenshot},
                  "comparison": comparison,
                  "capture_size_comparison": capture_comparison,
                  "screen_alignment": "unverified",
                  "action_safe": False,
                  "note": "Comparison does not calibrate or authorize coordinates; AT-SPI, X11 client and screenshot frame geometry can differ. Screenshot dimensions are compared separately from AT-SPI extents."}
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        return 0
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        print(f"geometry evidence collection failed: {message[:1000]}", file=sys.stderr)
        return 2
    except (subprocess.TimeoutExpired, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"cannot compare geometry: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
