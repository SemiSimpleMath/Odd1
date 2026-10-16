"""odd1-wiki: Odd1's knowledge base.

    odd1-wiki search TEXT...     sections matching all the words, active rules first, superseded ones last
    odd1-wiki show PAGE          a page (e.g. how-a-change-ships.md, decisions/0002-...)
    odd1-wiki rules [--all]      the decision records (active ones; --all with superseded)
"""
from __future__ import annotations

import sys

from . import wiki


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    try:
        if argv[0] == "search" and len(argv) > 1:
            hits = wiki.search(" ".join(argv[1:]))
            print("\n".join(wiki.format_hit(h) for h in hits) or "nothing found")
            return 0 if hits else 1
        if argv[0] == "show" and len(argv) == 2:
            print(wiki.show(argv[1]))
            return 0
        if argv[0] == "rules":
            for p in wiki.pages():
                if p["decision"] and (p["status"] == "active" or "--all" in argv):
                    print(f"[{p['status']}] {p['page']}: {p['title']} ({p['meta'].get('ticket', '')})")
            return 0
    except FileNotFoundError as e:
        print(f"odd1-wiki: {e}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
