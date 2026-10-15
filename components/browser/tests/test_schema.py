"""Contract tests for the versioned text and rendered JSON envelopes."""
import contextlib
import http.server
import io
import json
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from odd1_browser import text as browser
from odd1_browser import render as render_browser

SCHEMA = Path(render_browser.__file__).with_name("schema-v1.json")


class SchemaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import jsonschema
        except ImportError:
            raise unittest.SkipTest("jsonschema is not installed (module.nix provides it)")
        cls.schema = json.loads(SCHEMA.read_text())
        cls.validator = jsonschema.validators.validator_for(cls.schema)
        cls.validator.check_schema(cls.schema)
        cls.instance = cls.validator(cls.schema)
        cls.validation_error = jsonschema.ValidationError

    def assert_valid(self, value):
        self.instance.validate(value)

    def test_actual_text_cli_json_matches_schema(self):
        class Fixture(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<title>CLI fixture</title><h1>Actual</h1><p>Captured CLI JSON</p>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            completed = subprocess.run([sys.executable, "-m", "odd1_browser.cli", "text", url],
                                       capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            captured = json.loads(completed.stdout)
            self.assert_valid(captured)
            self.assertEqual(captured["page"]["title"], "CLI fixture")
            self.assertEqual(captured["page"]["headings"][0]["text"], "Actual")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_render_cli_json_matches_schema(self):
        rendered = {"schema_version": 1, "engine": "Firefox", "status": 200,
                    "page": {"title": "Rendered", "text": "JS output", "url": "http://127.0.0.1/",
                             "requested_url": "http://127.0.0.1/", "headings": [],
                             "headings_truncated": False, "links": [], "links_truncated": False,
                             "ready_state": "complete", "text_truncated": False}}
        stdout = io.StringIO()
        with patch.object(render_browser, "rendered_browse", return_value=rendered):
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(render_browser.main(["http://127.0.0.1/"]), 0)
        captured = json.loads(stdout.getvalue())
        self.assert_valid(captured)
        self.assertEqual(captured["page"]["title"], "Rendered")

    def test_text_browse_result_matches_schema_and_allows_additive_fields(self):
        body = b"<title>T</title><h1>H</h1><a href='/x'>X</a>"
        page = browser.extract("http://127.0.0.1/", "text/html", body)
        result = {"schema_version": 1, "url": "http://127.0.0.1/", "status": 200,
                  "content_type": "text/html", "bytes": len(body), "page": page,
                  "future_optional_field": {"x": True}}
        self.assert_valid(result)

    def test_non_html_text_result_matches_schema(self):
        self.assert_valid({"schema_version": 1, "url": "http://127.0.0.1/", "status": 200,
                           "content_type": "text/plain", "bytes": 3, "page": None})

    def test_render_result_matches_schema_and_additive_fields(self):
        expected = {"schema_version": 1, "engine": "Firefox", "status": 200,
                    "page": {"title": "x", "text": "y", "url": "http://127.0.0.1/",
                             "requested_url": "http://127.0.0.1/", "headings": [],
                             "headings_truncated": False, "links": [], "links_truncated": False,
                             "ready_state": "complete", "text_truncated": False}}
        self.assert_valid(expected | {"extra": "ignored by v1-compatible clients"})
        self.assert_valid({"schema_version": 1, "engine": "Firefox", "status": 200,
                           "page": {"title": "x", "text": "y", "url": "http://127.0.0.1/",
                                    "requested_url": "http://127.0.0.1/", "headings": [],
                                    "headings_truncated": False, "links": [], "links_truncated": False,
                                    "ready_state": "complete"}})

    def test_schema_rejects_missing_required_and_wrong_version(self):
        with self.assertRaises(self.validation_error):
            self.assert_valid({"schema_version": 1})
        with self.assertRaises(self.validation_error):
            self.assert_valid({"schema_version": 2, "engine": "Firefox", "status": 200, "page": {}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
