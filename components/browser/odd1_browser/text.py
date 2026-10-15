#!/usr/bin/env python3
"""Small, bounded, loopback-only text browser for Odd1 agents. No JS, cookies or writes."""
from __future__ import annotations

import argparse
import copy
import ipaddress
from email.message import Message
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

SCHEMA_VERSION = 1
MAX_BYTES = 1_000_000
TIMEOUT = 5
MAX_LINKS = 200
MAX_HEADINGS = 200
MAX_TEXT = 100_000
MAX_ELEMENT_TEXT = 4_096
MAX_HISTORY = 20


def _text_value(parts: list[str]) -> str:
    """Join bounded fragments without building an unbounded intermediate."""
    return (parts[0] if parts else "") + (" " + " ".join(parts[1:]) if len(parts) > 1 else "")


def _append_limited(parts: list[str], value: str, limit: int) -> bool:
    """Append a space-separated text fragment, bounding size and fragment count.

    Returns True if any part of value was omitted. Periodic compaction prevents
    adversarial markup with many tiny text nodes from retaining one Python
    object per node.
    """
    if not value:
        return False
    used = sum(map(len, parts)) + max(0, len(parts) - 1)
    separator = 1 if parts else 0
    remaining = limit - used - separator
    if remaining <= 0:
        return True
    omitted = len(value) > remaining
    parts.append(value[:remaining])
    if len(parts) > 128:
        parts[:] = [" ".join(parts)]
    return omitted


class BrowserError(Exception):
    """Expected refusal or fetch/parse error."""


def validate_url(url: str) -> str:
    """Validate and canonicalize a literal-loopback HTTP URL.

    Hostnames are deliberately rejected: validating a DNS answer separately
    from urllib's later connection would leave a DNS-rebinding time-of-check /
    time-of-use gap. Fragments are removed and the default port is omitted.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except (ValueError, TypeError) as e:
        raise BrowserError(f"invalid URL: {e}") from e
    if parsed.scheme.lower() != "http":
        raise BrowserError("only http URLs are supported")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise BrowserError("URL must have a loopback host and no credentials")
    if port is not None and not (1 <= port <= 65535):
        raise BrowserError("invalid port")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as e:
        raise BrowserError("only literal loopback IP destinations are allowed") from e
    if not address.is_loopback:
        raise BrowserError("only loopback destinations are allowed")
    host = address.compressed
    if address.version == 6:
        host = f"[{host}]"
    authority = host if port in (None, 80) else f"{host}:{port}"
    return urllib.parse.urlunsplit(("http", authority, parsed.path or "/", parsed.query, ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def fetch(url: str, *, timeout: float = TIMEOUT, max_bytes: int = MAX_BYTES,
          redirects: int = 5) -> tuple[str, int, str, bytes]:
    """Fetch a bounded GET, revalidating every redirect before following it."""
    current = validate_url(url)
    for hop in range(redirects + 1):
        req = urllib.request.Request(current, headers={"User-Agent": "odd1-browser/0.1", "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"}, method="GET")
        try:
            response = _OPENER.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            response = e
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise BrowserError(f"fetch failed: {e}") from e
        with response:
            status = response.getcode()
            location = response.headers.get("Location")
            if status in (301, 302, 303, 307, 308) and location:
                if hop >= redirects:
                    raise BrowserError("too many redirects")
                current = validate_url(urllib.parse.urljoin(current, location))
                continue
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise BrowserError(f"response exceeds {max_bytes} byte limit")
            return current, status, content_type, body
    raise BrowserError("too many redirects")


class _Extract(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.headings: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.links_truncated = False
        self.headings_truncated = False
        self.heading_text_truncated = False
        self.link_text_truncated = False
        self._heading_truncated = False
        self._link_truncated = False
        self._base_seen = False
        self._title = False
        self.title_truncated = False
        self.text_truncated = False
        self._heading: tuple[str, list[str]] | None = None
        self._ignore = 0
        self._link_href: str | None = None
        self._link_text: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        a = dict(attrs)
        if tag in ("script", "style", "noscript", "template"):
            self._ignore += 1
        if tag == "title":
            self._title = True
            self.title_parts = []
        if tag == "base" and not self._base_seen and a.get("href"):
            self._base_seen = True
            self.base = urllib.parse.urljoin(self.base, a["href"] or "")
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if len(self.headings) < MAX_HEADINGS:
                self._heading = (tag, [])
                self._heading_truncated = False
            else:
                self.headings_truncated = True
        if tag == "a" and a.get("href"):
            if len(self.links) < MAX_LINKS:
                href = urllib.parse.urljoin(self.base, a["href"] or "")
                self._link_href = href
                self._link_text = []
                self._link_truncated = False
            else:
                self.links_truncated = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("script", "style", "noscript", "template") and self._ignore:
            self._ignore -= 1
        if tag == "title":
            self._title = False
        if self._heading and tag == self._heading[0]:
            value = " ".join(_text_value(self._heading[1]).split())
            if value:
                self.headings.append({"level": self._heading[0], "text": value})
            self.heading_text_truncated |= self._heading_truncated
            self._heading = None
        if tag == "a" and self._link_href is not None:
            self.links.append({"text": " ".join(_text_value(self._link_text or []).split()), "url": self._link_href})
            self.link_text_truncated |= self._link_truncated
            self._link_href = None
            self._link_text = None

    def handle_data(self, data: str) -> None:
        if self._ignore:
            return
        if self._title:
            self.title_truncated |= _append_limited(self.title_parts, data, MAX_ELEMENT_TEXT)
        value = data.strip()
        if value:
            self.text_truncated |= _append_limited(self.text_parts, value, MAX_TEXT)
            if self._heading:
                self._heading_truncated |= _append_limited(self._heading[1], value, MAX_ELEMENT_TEXT)
            if self._link_href is not None:
                self._link_truncated |= _append_limited(self._link_text, value, MAX_ELEMENT_TEXT)


def _decode_html(body: bytes, content_type: str) -> tuple[str, str]:
    """Decode HTML conservatively, preferring HTTP charset over in-document hints."""
    message = Message()
    message["content-type"] = content_type
    try:
        charset = message.get_content_charset()
    except (LookupError, ValueError):
        charset = None
    if not charset:
        # HTML's encoding declaration is ASCII-compatible for the supported
        # encodings. Search only a bounded prefix, before interpreting the body.
        prefix = body[:4096].decode("ascii", errors="ignore")
        match = re.search(
            r"<meta\b[^>]*?charset\s*=\s*['\"]?\s*([A-Za-z0-9._:-]+)",
            prefix, re.IGNORECASE,
        )
        if not match:
            match = re.search(
                r"<meta\b[^>]*?content\s*=\s*['\"][^'\"]*?charset\s*=\s*([A-Za-z0-9._:-]+)",
                prefix, re.IGNORECASE,
            )
        charset = match.group(1) if match else "utf-8"
    try:
        return body.decode(charset, errors="replace"), charset
    except (LookupError, UnicodeError):
        # An invalid or unknown declaration must not make browsing fail.
        return body.decode("utf-8", errors="replace"), "utf-8 (fallback)"


def extract(url: str, content_type: str, body: bytes) -> dict[str, Any]:
    text, encoding = _decode_html(body, content_type)
    parser = _Extract(url)
    try:
        parser.feed(text)
        parser.close()
    except Exception as e:
        raise BrowserError(f"HTML parsing failed: {e}") from e
    title = " ".join(_text_value(parser.title_parts).split())
    page_text = _text_value(parser.text_parts)
    # Whitespace between text nodes is useful; remove spaces before common inline
    # punctuation so extraction reads naturally rather than "Orbit .".
    page_text = re.sub(r"\s+([.,!?;:])", r"\1", page_text)
    return {"title": title, "title_truncated": parser.title_truncated,
            "text": page_text, "text_truncated": parser.text_truncated,
            "encoding": encoding, "headings": parser.headings,
            "headings_truncated": parser.headings_truncated,
            "heading_text_truncated": parser.heading_text_truncated,
            "links": parser.links, "links_truncated": parser.links_truncated,
            "link_text_truncated": parser.link_text_truncated}


def find_text(page: dict[str, Any], query: str, *, context: int = 60,
              limit: int = 20) -> dict[str, Any]:
    """Find literal, case-insensitive matches in extracted page text.

    Offsets are Python string indices into the returned page text. If extraction
    truncated the document, ``complete`` is false: absence of a match then says
    nothing about the unreturned tail. Match and context bounds keep results
    compact for agent callers.
    """
    if not isinstance(query, str) or not query:
        raise BrowserError("query must be a non-empty string")
    if len(query) > 1000:
        raise BrowserError("query exceeds 1000 character limit")
    if not isinstance(context, int) or isinstance(context, bool) or not (0 <= context <= 500):
        raise BrowserError("context must be between 0 and 500")
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 100):
        raise BrowserError("limit must be between 1 and 100")
    text = page.get("text", "")
    if not isinstance(text, str):
        raise BrowserError("page text must be a string")
    matches = []
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    limit_reached = False
    for ordinal, match in enumerate(pattern.finditer(text)):
        if ordinal >= limit:
            limit_reached = True
            break
        start, end = match.span()
        excerpt_start = max(0, start - context)
        excerpt_end = min(len(text), end + context)
        matches.append({
            "start": start,
            "end": end,
            "excerpt": text[excerpt_start:excerpt_end],
            "excerpt_start": excerpt_start,
            "excerpt_end": excerpt_end,
            "prefix_omitted": excerpt_start > 0,
            "suffix_omitted": excerpt_end < len(text),
        })
    truncated = bool(page.get("text_truncated", False))
    return {"query": query, "matches": matches,
            "complete": not truncated and not limit_reached,
            "text_truncated": truncated,
            "match_limit_reached": limit_reached}


def browse(url: str) -> dict[str, Any]:
    final_url, status, content_type, body = fetch(url)
    result = {"schema_version": SCHEMA_VERSION, "url": final_url, "status": status,
              "content_type": content_type, "bytes": len(body)}
    if "html" in content_type.lower() or body.lstrip().lower().startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        result["page"] = extract(final_url, content_type, body)
    else:
        result["page"] = None
    return result


class BrowserSession:
    """Caller-owned navigation history; page contents are snapshots of fetches."""

    def __init__(self, *, max_history: int = MAX_HISTORY):
        if max_history < 1:
            raise ValueError("max_history must be at least 1")
        self.max_history = max_history
        self._history: list[dict[str, Any]] = []
        self._index = -1

    @property
    def current(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._history[self._index]) if self._index >= 0 else None

    @property
    def can_back(self) -> bool:
        return self._index > 0

    @property
    def can_forward(self) -> bool:
        return self._index + 1 < len(self._history)

    def open(self, url: str) -> dict[str, Any]:
        """Fetch URL and push it; a failed fetch leaves history unchanged."""
        normalized = validate_url(url)
        page = browse(normalized)
        del self._history[self._index + 1:]
        self._history.append(page)
        if len(self._history) > self.max_history:
            del self._history[0]
        self._index = len(self._history) - 1
        return self.current  # type: ignore[return-value]

    def follow_link(self, link_id: int) -> dict[str, Any]:
        """Follow a zero-based link ID from the current page through URL policy."""
        if self._index < 0:
            raise BrowserError("no current page")
        links = (self._history[self._index].get("page") or {}).get("links", [])
        if not isinstance(link_id, int) or isinstance(link_id, bool) or not (0 <= link_id < len(links)):
            raise BrowserError("invalid link ID")
        return self.open(links[link_id]["url"])

    def back(self) -> dict[str, Any]:
        if not self.can_back:
            raise BrowserError("no previous page")
        self._index -= 1
        return self.current  # type: ignore[return-value]

    def forward(self) -> dict[str, Any]:
        if not self.can_forward:
            raise BrowserError("no next page")
        self._index += 1
        return self.current  # type: ignore[return-value]

    def history(self) -> dict[str, Any]:
        """Return a compact versioned history index; page snapshots stay separate."""
        return {"schema_version": SCHEMA_VERSION, "index": self._index, "items": [
            {"url": item["url"], "title": (item.get("page") or {}).get("title", "")}
            for item in self._history
        ]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args(argv)
    try:
        result = browse(args.url)
    except BrowserError as e:
        print(f"odd1-browser text: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if 200 <= result["status"] < 400 else 1


if __name__ == "__main__":
    raise SystemExit(main())
