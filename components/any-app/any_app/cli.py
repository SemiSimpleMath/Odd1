"""any-app command line: one command, one subcommand per observation tool.

    any-app describe WINDOW        AT-SPI tree of one window (title substring) as compact JSON
    any-app ocr PNG                Tesseract words with image-local boxes and confidence
    any-app screenshot WINDOW      PNG of one window, even when covered by others (-o OUT, --frame); --screen: all
    any-app geometry WINDOW        AT-SPI vs X11 vs screenshot geometry for one window
    any-app eval-ocr OCR LABELS    token precision/recall/F1 of an OCR result against labels
    any-app eval-boxes OCR LABELS  box localization (IoU) of an OCR result against labelled boxes
"""
import sys

from . import (compare_geometry, describe_accessible, describe_pixels, evaluate_ocr, evaluate_ocr_localization,
               screenshot)

COMMANDS = {
    "describe": describe_accessible.main,
    "ocr": describe_pixels.main,
    "screenshot": screenshot.main,
    "geometry": compare_geometry.run,
    "eval-ocr": evaluate_ocr.main,
    "eval-boxes": evaluate_ocr_localization.main,
}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMMANDS:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
