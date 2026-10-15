"""odd1-browser: an agent's view of web pages, from the command line. Prints JSON (schema-v1.json).

    odd1-browser text URL                        fetch and extract, no JavaScript
    odd1-browser render URL [--wait-ms N] [--screenshot] [--visible]
                 [--actions-json '[{"id":"el:0003","action":"click"}]'] [--allow-form-post]
                                                 render in Firefox, apply actions, print the page view

URLs are literal-loopback http only (e.g. http://127.0.0.1:8000/).
"""
import sys

from . import render, text


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("text", "render"):
        print(__doc__, file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    return text.main(rest) if command == "text" else render.main(rest)


if __name__ == "__main__":
    sys.exit(main())
