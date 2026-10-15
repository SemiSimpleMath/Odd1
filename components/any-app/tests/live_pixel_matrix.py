#!/usr/bin/env python3
"""Live Orbit Desktop OCR robustness probe; disposable Tk fixtures only.

Requires DISPLAY, tkinter, ImageMagick `import`, Tesseract, and describe_pixels.py.
Does not retain images/results or authorize actions. Intended to be copied with
its sibling script to a private temporary directory and cleaned after a run.
"""
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import tkinter as tk

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("describe_pixels", HERE.parent / "any_app" / "describe_pixels.py")
pixels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pixels)
contract_spec = importlib.util.spec_from_file_location("pixel_matrix_contract", HERE / "pixel_matrix_contract.py")
contract = importlib.util.module_from_spec(contract_spec)
contract_spec.loader.exec_module(contract)

# Each record: name, text, font size, width, height, background, foreground,
# expected tokens, oracle mode. Diagnostic cases are deliberately not promoted
# into OCR pass/fail thresholds until a representative corpus exists.
SCENARIOS = [
    ("small", "violet lighthouse 482", 12, 720, 180, "#ffffff", "#000000",
     ("violet", "lighthouse", "482"), "tokens"),
    ("medium", "cobalt garden 731", 22, 720, 220, "#172554", "#ffffff",
     ("cobalt", "garden", "731"), "tokens"),
    ("large_wrapped", "amber compass\n905 north", 30, 720, 260, "#eeeeee", "#222222",
     ("amber", "compass", "905", "north"), "tokens"),
    ("low_contrast", "muted signal 364", 20, 720, 220, "#aaaaaa", "#777777",
     ("muted", "signal", "364"), "diagnostic"),
    ("blank", "", 20, 720, 180, "#ffffff", "#000000", (), "empty"),
    ("noisy_blank", "", 20, 720, 220, "#f3f0e8", "#222222", (), "diagnostic"),
]


def run():
    summaries = []
    with tempfile.TemporaryDirectory(prefix="orbit-pixel-matrix-") as temp:
        os.chmod(temp, 0o700)
        root = tk.Tk()
        root.title("Orbit disposable OCR matrix")
        root.configure(bg="white")
        canvas = tk.Canvas(root, highlightthickness=0, bd=0)
        canvas.pack(expand=True, fill="both")
        label = tk.Label(canvas, text="", font=("DejaVu Sans", 12), bg="white", fg="black", justify="center")
        label.place(relx=0.5, rely=0.5, anchor="center")
        noise = []
        root.geometry("720x260+40+40")
        root.update()
        wid = str(root.winfo_id())
        try:
            for name, text, size, width, height, background, foreground, expected, oracle in SCENARIOS:
                root.geometry(f"{width}x{height}+40+40")
                root.configure(bg=background)
                canvas.configure(bg=background)
                label.configure(text=text, font=("DejaVu Sans", size), bg=background, fg=foreground)
                for item_id in noise:
                    canvas.delete(item_id)
                noise.clear()
                if name == "noisy_blank":
                    rng = random.Random(74291)
                    # Fixed-seed low-contrast clutter: strokes, tiny glyphs and dots.
                    for _ in range(100):
                        x, y = rng.randrange(width), rng.randrange(height)
                        shade = rng.choice(("#ddd9d0", "#e5e0d7", "#ece7de"))
                        noise.append(canvas.create_line(x, y, x + rng.randrange(-12, 13),
                                                        y + rng.randrange(-3, 4), fill=shade, width=1))
                    for _ in range(24):
                        x, y = rng.randrange(width), rng.randrange(height)
                        noise.append(canvas.create_text(x, y, text=rng.choice(("·", "-", ".", "'")),
                                                        fill="#dedad1", font=("DejaVu Sans", 9)))
                root.update_idletasks()
                root.update()
                png = Path(temp) / f"{name}.png"
                subprocess.run(["import", "-window", wid, f"PNG:{png}"], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
                os.chmod(png, 0o600)
                result = pixels.describe_image(png, timeout=15)
                words = [item["text"].lower() for item in result["elements"]]
                oracle_result = contract.evaluate_oracle(words, expected, oracle)
                # Do not demand low-contrast recall or assume OCR false-positive
                # rates. Record those observations distinctly as diagnostics.
                assert oracle_result["passed"], {"fixture": name,
                                                 "missing": oracle_result["missing_tokens"],
                                                 "observed_tokens": oracle_result["observed_tokens"],
                                                 "ocr_word_count": oracle_result["ocr_word_count"],
                                                 "observed_tokens_truncated": oracle_result["observed_tokens_truncated"]}
                assert result["image"]["width"] == width and result["image"]["height"] == height
                assert result["geometry"]["screen_alignment"] == "unverified"
                assert result["action_safe"] is False
                for item in result["elements"]:
                    b = item["bounds"]
                    assert 0 <= b["x"] and 0 <= b["y"] and b["x"] + b["width"] <= width and b["y"] + b["height"] <= height
                summaries.append({"fixture": name, "oracle": oracle,
                                  "oracle_passed": oracle_result["passed"],
                                  "expected_tokens": list(expected),
                                  "missing_tokens": oracle_result["missing_tokens"],
                                  "unexpected_tokens": oracle_result["unexpected_tokens"],
                                  "unexpected_token_count": oracle_result["unexpected_token_count"],
                                  "unexpected_tokens_truncated": oracle_result["unexpected_tokens_truncated"],
                                  "observed_tokens": oracle_result["observed_tokens"],
                                  "observed_token_count": oracle_result["observed_token_count"],
                                  "observed_tokens_truncated": oracle_result["observed_tokens_truncated"],
                                  "ocr_word_count": oracle_result["ocr_word_count"],
                                  "confidence_min": min((w["confidence"] for w in result["elements"]), default=None),
                                  "confidence_max": max((w["confidence"] for w in result["elements"]), default=None),
                                  "image_sha256": result["image"]["sha256"],
                                  "bounds_in_image": True, "action_safe": False,
                                  "screen_alignment": "unverified"})
        finally:
            root.destroy()
    print(json.dumps({"passed": True, "fixtures": summaries}, separators=(",", ":")))


if __name__ == "__main__":
    run()
