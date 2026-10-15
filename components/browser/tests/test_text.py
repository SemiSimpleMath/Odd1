#!/usr/bin/env python3
"""Offline tests for the loopback-only text browser (odd1_browser.text)."""
import http.server
import json
import subprocess
import sys
import threading
import unittest
from urllib.parse import urlsplit

from odd1_browser import text as browser


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        routes = {
            "/": (200, "text/html; charset=utf-8", b'<!doctype html><html><head><title>Fixture &amp; page</title><script>hidden()</script></head><body><h1>Welcome</h1><p>Hello <b>Orbit</b>.</p><a href="/next?q=1">Go there</a><a href="http://127.0.0.1:1/no">Unreachable</a></body></html>'),
            "/next?q=1": (200, "text/html", b"<title>Next</title><h2>Arrived</h2>"),
            "/redirect": (302, "text/plain", b""),
            "/external": (302, "text/plain", b""),
            "/absolute": (302, "text/plain", b""),
            "/big": (200, "text/html", b"x" * 40),
            "/plain": (200, "text/plain", b"not html"),
            "/legacy": (200, "text/html", b'<meta charset="iso-8859-1"><title>caf\xe9</title><p>caf\xe9</p>'),
            "/http-charset": (200, "text/html; charset=iso-8859-1", b'<meta charset="utf-8"><p>caf\xe9</p>'),
            "/many-links": (200, "text/html", (b'<a href="/x">x</a>' * (browser.MAX_LINKS + 2)) + (b'<h1>x</h1>' * (browser.MAX_HEADINGS + 1))),
            "/basepage": (200, "text/html", b'<base href="/other/"><base href="/ignored/"><a href="x">X</a>'),
        }
        if self.path in ("/redirect", "/external", "/absolute"):
            self.send_response(302)
            location = {"/redirect": "/next?q=1", "/external": "http://example.com/",
                        "/absolute": f"http://127.0.0.1:{self.server.server_port}/next?q=1"}[self.path]
            self.send_header("Location", location)
            self.end_headers()
            return
        status, ctype, body = routes.get(self.path, (404, "text/plain", b"missing"))
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_extract_title_text_headings_links_and_suppression(self):
        result = browser.browse(self.base + "/")
        self.assertEqual(result["schema_version"], 1)
        page = result["page"]
        self.assertEqual(page["title"], "Fixture & page")
        self.assertIn("Hello Orbit.", page["text"])
        self.assertNotIn("hidden", page["text"])
        self.assertEqual(page["headings"], [{"level": "h1", "text": "Welcome"}])
        self.assertEqual(page["links"][0], {"text": "Go there", "url": self.base + "/next?q=1"})

    def test_relative_redirect_validated_and_followed(self):
        response = browser.browse(self.base + "/redirect")
        self.assertEqual(response["url"], self.base + "/next?q=1")
        self.assertEqual(response["page"]["title"], "Next")

    def test_absolute_redirect_and_url_canonicalization(self):
        response = browser.browse(self.base + "/absolute")
        self.assertEqual(response["url"], self.base + "/next?q=1")
        self.assertEqual(browser.validate_url("HTTP://127.0.0.1:80/a#frag"), "http://127.0.0.1/a")
        self.assertEqual(browser.validate_url("http://127.0.0.1:00080/a"), "http://127.0.0.1/a")
        self.assertEqual(browser.validate_url("http://[::1]:80/a#x"), "http://[::1]/a")

    def test_external_redirect_rejected_before_connection(self):
        with self.assertRaisesRegex(browser.BrowserError, "loopback"):
            browser.browse(self.base + "/external")

    def test_rejects_external_schemes_credentials_and_hosts(self):
        for url in ("https://127.0.0.1/", "file:///etc/passwd", "http://example.com/", "http://orbit.local/", "http://user@127.0.0.1/"):
            with self.subTest(url=url), self.assertRaises(browser.BrowserError):
                browser.validate_url(url)

    def test_response_limit_and_non_html(self):
        with self.assertRaisesRegex(browser.BrowserError, "exceeds"):
            browser.fetch(self.base + "/big", max_bytes=8)
        result = browser.browse(self.base + "/plain")
        self.assertIsNone(result["page"])

    def test_malformed_unclosed_markup_is_tolerated(self):
        page = browser.extract(self.base + "/broken", "text/html",
                               b"<title>Broken</title><p>One <b>two</p><h2>Heading")
        self.assertEqual(page["text"], "Broken One two Heading")
        self.assertEqual(page["headings"], [])

    def test_adversarial_malformed_markup_has_deterministic_bounded_results(self):
        # These deliberately invalid snippets characterize stdlib HTMLParser,
        # not HTML5 tree-building semantics. They must remain safe and bounded.
        fixtures = {
            "unclosed_comment": b"<p>visible</p><!-- <p>comment content",
            "duplicate_attributes": b'<a href="/first" href="/second">Link</a>',
            "nested_headings": b"<h1>outer <h2>inner</h2> tail</h1>",
            "stray_void_end_tag": b"before<br>after</img><h2>end</h2>",
        }
        pages = {name: browser.extract(self.base, "text/html", markup)
                 for name, markup in fixtures.items()}
        for page in pages.values():
            self.assertLessEqual(len(page["text"]), browser.MAX_TEXT)
            self.assertLessEqual(len(page["headings"]), browser.MAX_HEADINGS)
            self.assertLessEqual(len(page["links"]), browser.MAX_LINKS)
        self.assertIn("visible", pages["unclosed_comment"]["text"])
        self.assertEqual(pages["duplicate_attributes"]["links"][0]["url"],
                         self.base + "/second")
        self.assertEqual(pages["nested_headings"]["headings"],
                         [{"level": "h2", "text": "inner"}])
        self.assertEqual(pages["stray_void_end_tag"]["text"], "before after end")

    def test_adversarial_element_and_text_buffers_are_bounded(self):
        body = ("<title>" + "T" * (browser.MAX_ELEMENT_TEXT + 100) + "</title>"
                + "<h1>" + "H" * (browser.MAX_ELEMENT_TEXT + 100) + "</h1>"
                + "<a href='/'>" + "L" * (browser.MAX_ELEMENT_TEXT + 100) + "</a>"
                + "<p>" + ("word " * (browser.MAX_TEXT // 4 + 200)) + "</p>")
        page = browser.extract(self.base, "text/html", body.encode())
        self.assertEqual(len(page["title"]), browser.MAX_ELEMENT_TEXT)
        self.assertTrue(page["title_truncated"])
        self.assertEqual(len(page["headings"][0]["text"]), browser.MAX_ELEMENT_TEXT)
        self.assertTrue(page["heading_text_truncated"])
        self.assertEqual(len(page["links"][0]["text"]), browser.MAX_ELEMENT_TEXT)
        self.assertTrue(page["link_text_truncated"])
        self.assertLessEqual(len(page["text"]), browser.MAX_TEXT)
        self.assertTrue(page["text_truncated"])

    def test_many_tiny_text_nodes_compact_parser_fragments(self):
        markup = "<p>" + "<b>x</b>" * 60_000 + "</p>"
        page = browser.extract(self.base, "text/html", markup.encode())
        self.assertLessEqual(len(page["text"]), browser.MAX_TEXT)
        self.assertTrue(page["text_truncated"])

    def test_charset_http_precedence_meta_fallback_and_invalid_fallback(self):
        meta = browser.browse(self.base + "/legacy")["page"]
        self.assertEqual(meta["encoding"].lower(), "iso-8859-1")
        self.assertEqual(meta["title"], "café")
        self.assertIn("café", meta["text"])
        http = browser.browse(self.base + "/http-charset")["page"]
        self.assertEqual(http["encoding"].lower(), "iso-8859-1")
        self.assertIn("café", http["text"])
        fallback = browser.extract(self.base, "text/html; charset=unknown-codec", b"<p>hello</p>")
        self.assertEqual(fallback["encoding"], "utf-8 (fallback)")
        self.assertEqual(fallback["text"], "hello")

    def test_base_url_and_link_heading_bounds_are_reported(self):
        page = browser.browse(self.base + "/basepage")["page"]
        self.assertEqual(page["links"][0]["url"], self.base + "/other/x")
        result = browser.browse(self.base + "/many-links")["page"]
        self.assertEqual(len(result["links"]), browser.MAX_LINKS)
        self.assertTrue(result["links_truncated"])
        self.assertEqual(len(result["headings"]), browser.MAX_HEADINGS)
        self.assertTrue(result["headings_truncated"])

    def test_session_navigation_branching_and_isolation(self):
        first = browser.BrowserSession()
        second = browser.BrowserSession()
        self.assertIsNone(first.current)
        page1 = first.open(self.base + "/")
        self.assertEqual(page1["url"], self.base + "/")
        with self.assertRaisesRegex(browser.BrowserError, "invalid link"):
            first.follow_link(-1)
        page2 = first.follow_link(0)
        self.assertEqual(page2["url"], self.base + "/next?q=1")
        self.assertTrue(first.can_back)
        self.assertEqual(first.back()["url"], page1["url"])
        self.assertEqual(first.forward()["url"], page2["url"])
        first.back()
        first.open(self.base + "/plain")
        self.assertFalse(first.can_forward)
        with self.assertRaisesRegex(browser.BrowserError, "no next"):
            first.forward()
        self.assertIsNone(second.current)
        self.assertEqual(len(first.history()["items"]), 2)

    def test_session_failed_navigation_atomicity_and_bounded_history(self):
        session = browser.BrowserSession(max_history=2)
        first = session.open(self.base + "/")
        with self.assertRaises(browser.BrowserError):
            session.open("http://example.com/")
        self.assertEqual(session.current["url"], first["url"])
        session.open(self.base + "/next?q=1")
        session.open(self.base + "/plain")
        self.assertEqual(len(session.history()["items"]), 2)
        self.assertEqual(session.history()["index"], 1)
        self.assertEqual(session.back()["url"], self.base + "/next?q=1")
        with self.assertRaisesRegex(browser.BrowserError, "no previous"):
            session.back()
        with self.assertRaisesRegex(browser.BrowserError, "no current"):
            browser.BrowserSession().follow_link(0)

    def test_find_text_literal_offsets_context_and_completeness(self):
        page = {"text": "Alpha needle beta NEEDLE gamma", "text_truncated": False}
        result = browser.find_text(page, "needle", context=3, limit=5)
        self.assertEqual([(m["start"], m["end"]) for m in result["matches"]], [(6, 12), (18, 24)])
        self.assertEqual(result["matches"][0]["excerpt"], "ha needle be")
        self.assertTrue(result["matches"][0]["prefix_omitted"])
        self.assertTrue(result["matches"][0]["suffix_omitted"])
        self.assertTrue(result["complete"])
        self.assertFalse(result["match_limit_reached"])

    def test_extraction_marks_text_truncation_for_find(self):
        page = browser.extract(self.base + "/", "text/html; charset=utf-8",
                               (b"<p>alpha sentence.</p>" * 8000))
        self.assertLessEqual(len(page["text"]), browser.MAX_TEXT)
        self.assertTrue(page["text_truncated"])
        found = browser.find_text(page, "missing")
        self.assertFalse(found["complete"])
        self.assertTrue(found["text_truncated"])

    def test_find_text_limits_and_truncated_page_uncertainty(self):
        page = {"text": "x x x", "text_truncated": False}
        result = browser.find_text(page, "x", context=0, limit=2)
        self.assertEqual(len(result["matches"]), 2)
        self.assertTrue(result["match_limit_reached"])
        self.assertFalse(result["complete"])
        # Exactly reaching the limit is not the same as there being more matches.
        exact = browser.find_text({"text": "x x", "text_truncated": False}, "x", limit=2)
        self.assertFalse(exact["match_limit_reached"])
        self.assertTrue(exact["complete"])
        partial = browser.find_text({"text": "x", "text_truncated": True}, "absent")
        self.assertEqual(partial["matches"], [])
        self.assertFalse(partial["complete"])
        self.assertTrue(partial["text_truncated"])
        with self.assertRaisesRegex(browser.BrowserError, "non-empty"):
            browser.find_text(page, "")
        with self.assertRaisesRegex(browser.BrowserError, "context"):
            browser.find_text(page, "x", context=501)

    def test_cli_json_and_refusal(self):
        cli = [sys.executable, "-m", "odd1_browser.cli", "text"]
        ok = subprocess.run(cli + [self.base + "/"], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["page"]["title"], "Fixture & page")
        bad = subprocess.run(cli + ["http://example.com/"], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("loopback", bad.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
