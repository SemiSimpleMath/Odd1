"""The wiki: Odd1's knowledge base, Markdown in the repository (wiki/), searched by page and section.

Explanatory pages (wiki/*.md) are fixed by any agent that finds one wrong; decision records (wiki/decisions/*.md)
carry front matter (title, status active|superseded, superseded_by, ticket, date) and are the person's to change.
Search ranks active decision records first and superseded ones last, marked. The wiki read is the deployed one
(committed and shipped: /var/lib/odd1/deploy/current/wiki), else the repository's.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

CANDIDATES = [os.environ.get("ODD1_WIKI", ""), "/var/lib/odd1/deploy/current/wiki", "/srv/odd1/wiki"]
WORD = re.compile(r"[a-z0-9][a-z0-9\-_.]*")


def root() -> Path:
    for c in CANDIDATES:
        if c and Path(c, "index.md").exists():
            return Path(c)
    raise FileNotFoundError("no wiki found (set ODD1_WIKI)")


def front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    head, _, body = text[4:].partition("\n---\n")
    meta = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body


def pages(base: Path | None = None) -> list[dict]:
    base = base or root()
    out = []
    for path in sorted(base.rglob("*.md")):
        meta, body = front_matter(path.read_text(encoding="utf-8"))
        rel = str(path.relative_to(base))
        title = meta.get("title") or next((l.lstrip("# ").strip() for l in body.splitlines() if l.startswith("#")),
                                          path.stem)
        out.append({"page": rel, "title": title, "meta": meta, "body": body,
                    "decision": rel.startswith("decisions/"), "status": meta.get("status", "")})
    return out


def sections(body: str) -> list[tuple[str, str]]:
    parts, heading, lines = [], "", []
    for line in body.splitlines():
        if line.startswith("#"):
            if lines:
                parts.append((heading, "\n".join(lines)))
            heading, lines = line.lstrip("# ").strip(), []
        else:
            lines.append(line)
    parts.append((heading, "\n".join(lines)))
    return parts


def search(query: str, base: Path | None = None, limit: int = 8) -> list[dict]:
    """Sections matching the query's words, best first: active decision records lead, superseded ones trail."""
    words = WORD.findall(query.lower())
    hits = []
    for p in pages(base):
        for heading, text in sections(p["body"]):
            hay = f"{p['title']} {heading} {text}".lower()
            score = sum(hay.count(w) for w in words)
            if not score or not all(w in hay for w in words):
                continue
            if p["decision"]:
                score += 1000 if p["status"] == "active" else -1000
            line = next((l.strip() for l in text.splitlines() if any(w in l.lower() for w in words)), "")
            hits.append({"page": p["page"], "title": p["title"], "section": heading, "score": score,
                         "status": p["status"] or ("page" if not p["decision"] else "?"),
                         "superseded_by": p["meta"].get("superseded_by") or None, "line": line[:200]})
    return sorted(hits, key=lambda h: -h["score"])[:limit]


def show(page: str, base: Path | None = None) -> str:
    base = base or root()
    path = (base / page).resolve()
    if base.resolve() not in path.parents or not path.exists():
        raise FileNotFoundError(f"no wiki page {page}")
    return path.read_text(encoding="utf-8")


def format_hit(h: dict) -> str:
    mark = {"active": "[active rule]", "superseded": f"[SUPERSEDED by {h['superseded_by'] or '?'}]",
            "page": "[page]"}.get(h["status"], f"[{h['status']}]")
    section = f" > {h['section']}" if h["section"] and h["section"] != h["title"] else ""
    return f"{mark} {h['page']}: {h['title']}{section}\n    {h['line']}"
