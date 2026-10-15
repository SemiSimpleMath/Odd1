"""Opt-in, real-Firefox integration checks for the validating proxy.

Run in the documented Nix shell on a host with Firefox/geckodriver:
  python3 -m unittest -v test_render_firefox
The test records destinations presented by Firefox to the local proxy. The
external test host is deliberately fake and must be rejected before any connect.
"""
import base64
import http.server
import json
import subprocess
import sys
import threading
import unittest
from unittest import mock

from odd1_browser import text as browser
from odd1_browser import render as render_browser


class _Fixture(http.server.BaseHTTPRequestHandler):
    seen = []
    lock = threading.Lock()

    def do_GET(self):
        with self.lock:
            self.seen.append(self.path)
        if self.path == "/":
            # The task link is created by page JavaScript; the integration flow
            # therefore exercises rendered JS content, not just static HTML.
            body = b"""<!doctype html><title>Index</title><h1>Agent browser demo</h1>
              <script>const p=document.createElement('p');p.textContent='JavaScript is ready';document.body.append(p);
              const a=document.createElement('a');a.href='/form';a.textContent='Feedback form';document.body.append(a);</script>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path == "/post-redirect-form":
            body = b"""<!doctype html><title>POST redirect</title>
              <form id='redirect-form' method='post' action='/post-redirect'>
                <input name='confirm' value='yes'><button id='redirect-send' name='go' value='1'>Continue</button>
              </form>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path == "/form":
            body = b"""<!doctype html><title>Feedback form</title><h1>Feedback</h1>
              <form id='feedback' method='post' action='/result'>
                <label>Name <input id='name' name='name' type='text'></label>
                <label>Topic <select id='topic' name='topic'><option value='a'>Alpha</option><option value='b'>Beta</option></select></label>
                <button id='send' type='submit'>Send</button>
              </form>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path == "/form-complex":
            # Differential form-serialization fixture: duplicate names, disabled
            # controls, textarea line endings, reserved characters and submitter.
            body = b"""<!doctype html><title>Complex form</title>
              <form id='complex' method='post' action='/result'>
                <input name='dup' value='first'><input name='dup' value='second'>
                <input name='disabled' value='must-not-submit' disabled>
                <input name='linebreak' value='line one&#10;line two'>
                <textarea id='notes' name='notes'>line one\nline two &amp; caf\xc3\xa9</textarea>
                <button id='complex-send' name='submitter' value='save'>Save</button>
              </form>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path == "/action-guards":
            body = b"""<!doctype html><title>Action guards</title>
              <button id='disabled' disabled>Disabled</button>
              <button id='hidden' style='display:none'>Hidden</button>
              <input id='readonly' value='fixed' readonly>
              <div id='not-control'>Not a control</div>
              <form id='hidden-form' style='display:none' method='post' action='/result'><button type='submit'>Hidden send</button></form>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path == "/download":
            body = b"must-not-be-downloaded"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="payload.bin"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif self.path == "/redirect-external":
            self.send_response(302)
            self.send_header("Location", "http://example.com/orbit-denied-redirect")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif self.path == "/navigate-external":
            body = b"""<!doctype html><title>local start</title><script>
              setTimeout(() => { location.href = 'http://example.com/orbit-denied-top-level'; }, 100);
            </script>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path == "/after-post-redirect":
            body = b"<!doctype html><title>Redirected</title><h1>POST became GET</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path == "/pixel":
            body = b"GIF89a"
            self.send_response(200)
            self.send_header("Content-Type", "image/gif")
        elif self.path == "/page":
            body = b"""<!doctype html><title>before</title><body>
              <h1>before</h1><img src='/pixel'>
              <img src='http://example.com/orbit-denied.png'>
              <img src='https://https-denied.invalid/orbit-denied.png'>
              <script>
                document.title = 'after-js';
                document.querySelector('h1').textContent = 'rendered by Firefox';
                fetch('http://example.com/orbit-denied-fetch').catch(() => {});
                try { new WebSocket('wss://websocket-denied.invalid/orbit-denied'); } catch (_) {}
              </script>
            </body>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        else:
            self.send_response(404)
            body = b"missing"
            self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        with self.lock:
            self.seen.append(self.path)
        size = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(size)
        self.server.submitted = raw.decode("utf-8")
        self.server.post_paths = getattr(self.server, "post_paths", []) + [self.path]
        if self.path == "/post-redirect":
            self.server.redirect_post_body = raw
            self.send_response(303)
            self.send_header("Location", "/after-post-redirect")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"<!doctype html><title>Submitted</title><h1 id='thank-you'>Thank you</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class FirefoxProxyIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import selenium  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("Selenium is unavailable (module.nix provides it: run odd1-browser-test on the Odd1 Desktop)")
        import shutil
        if not shutil.which("geckodriver"):
            raise unittest.SkipTest("geckodriver unavailable (module.nix provides it: run odd1-browser-test on the Odd1 Desktop)")
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Fixture)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.server.allowed_post_origin = ("http", "127.0.0.1", cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "server"):
            cls.server.shutdown()
            cls.server.server_close()
            cls.thread.join(timeout=2)

    def test_real_firefox_js_redirect_assets_and_external_proxy_denial(self):
        destinations = []
        upstream_targets = []
        original_proxy = render_browser._LoopbackProxy
        original_open = render_browser._DIRECT.open

        class RecordingProxy(original_proxy):
            def do_GET(self):
                destinations.append(self.path)
                super().do_GET()

            def do_CONNECT(self):
                # HTTPS is represented as CONNECT host:port, not an absolute
                # https:// URL handled by do_GET. Record attempted tunnel too.
                destinations.append("CONNECT " + self.path)
                super().do_CONNECT()

        def recording_open(request, *args, **kwargs):
            upstream_targets.append(request.full_url)
            return original_open(request, *args, **kwargs)

        with _Fixture.lock:
            _Fixture.seen.clear()
        with mock.patch.object(render_browser, "_LoopbackProxy", RecordingProxy), mock.patch.object(
                render_browser._DIRECT, "open", side_effect=recording_open):
            result = render_browser.rendered_browse(self.base + "/redirect", wait_ms=1500)

        page = result["page"]
        self.assertEqual(page["url"], self.base + "/page")
        self.assertEqual(page["title"], "after-js")
        self.assertIn("rendered by Firefox", page["text"])
        with _Fixture.lock:
            upstream_paths = list(_Fixture.seen)
        self.assertIn("/redirect", upstream_paths)
        self.assertIn("/page", upstream_paths)
        self.assertIn("/pixel", upstream_paths)
        # Explicitly verify Firefox did not bypass the configured proxy for a
        # literal-localhost asset. The upstream fixture alone would also see a
        # direct request, so require the proxy's observer to record its URL.
        self.assertIn(self.base + "/pixel", destinations, destinations)
        # Firefox actually sent an external subresource to our proxy. Its policy
        # validator rejects hostnames before opening an upstream connection.
        self.assertTrue(any("example.com/orbit-denied" in target for target in destinations), destinations)
        # HTTPS is directed at the validating proxy and refused; WSS is not an
        # allowed protocol. These observations are not proof against bypass.
        # Firefox may represent HTTPS via CONNECT or suppress the mixed-content
        # request before proxying it. In either case it must not reach upstream.
        self.assertFalse(any("https-denied.invalid" in target for target in upstream_targets), upstream_targets)
        self.assertFalse(any("example.com" in target for target in upstream_targets), upstream_targets)
        self.assertFalse(any("websocket-denied.invalid" in target for target in upstream_targets), upstream_targets)

    def test_actions_refuse_disabled_hidden_readonly_and_noninteractive_targets(self):
        def cli(url, action):
            completed = subprocess.run(
                [sys.executable, "-m", "odd1_browser.cli", "render", url, "--wait-ms", "0",
                 "--actions-json", json.dumps([action])],
                check=False, capture_output=True, text=True, timeout=90)
            self.assertEqual(completed.returncode, 2, completed.stdout)
            return completed.stderr

        cases = [
            ({"id": "id:disabled", "action": "click"}, "disabled elements"),
            ({"id": "id:hidden", "action": "click"}, "hidden elements"),
            ({"id": "id:readonly", "action": "type", "value": "changed"}, "readonly inputs"),
            ({"id": "id:not-control", "action": "click"}, "not an interactive element"),
            ({"id": "id:hidden-form", "action": "submit"}, "hidden elements"),
        ]
        for action, message in cases:
            with self.subTest(action=action):
                self.assertIn(message, cli(self.base + "/action-guards", action))
        with _Fixture.lock:
            self.assertNotIn("/result", _Fixture.seen)

    def test_agent_interacts_by_stable_ids_and_reads_form_result(self):
        # Exercise the public CLI as an agent would: read each JSON page, follow
        # its reported link, then submit declarative actions by stable ID.
        def cli(url, actions=None, allow_form_post=False):
            command = [sys.executable, "-m", "odd1_browser.cli", "render", url, "--wait-ms", "250"]
            if actions is not None:
                command.extend(["--actions-json", json.dumps(actions)])
            if allow_form_post:
                command.append("--allow-form-post")
            completed = subprocess.run(command, check=False, capture_output=True,
                                       text=True, timeout=90)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(completed.stdout)

        start = cli(self.base + "/")
        self.assertEqual(start["page"]["title"], "Index")
        self.assertIn("JavaScript is ready", start["page"]["text"])
        link = next(link for link in start["page"]["links"] if link["text"] == "Feedback form")
        self.assertEqual(link["url"], self.base + "/form")
        form = cli(link["url"])
        self.assertTrue({"id:name", "id:topic", "id:send"}.issubset({item["id"] for item in form["page"]["stable_ids"]}))
        result = cli(link["url"], [
            {"id":"id:name", "action":"type", "value":"Ada"},
            {"id":"id:topic", "action":"select", "value":"b"},
            {"id":"id:send", "action":"submit"},
        ], allow_form_post=True)
        self.assertEqual(result["page"]["url"], self.base + "/result")
        self.assertEqual(result["page"]["title"], "Submitted")
        self.assertTrue(any(item["id"] == "id:thank-you" for item in result["page"]["stable_ids"]))
        self.assertIn("Thank you", result["page"]["text"])
        self.assertEqual(self.server.submitted, "name=Ada&topic=b")
        self.assertEqual([item["action"] for item in result["actions"]], ["type", "select", "submit"])

    def test_native_firefox_form_serialization_matches_bound_permit(self):
        # Observe the actual browser POST bytes at the policy proxy, then compare
        # them to the permit's prepared bytes and the fixture's received payload.
        observed = {}
        original_server = render_browser._BoundedThreadingHTTPServer
        class RecordingServer(original_server):
            def grant_post_permit(self, target, expected_body):
                observed["granted_target"] = target
                observed["granted_body"] = expected_body
                return super().grant_post_permit(target, expected_body)
            def consume_post_permit(self, target, body):
                observed["target"] = target
                observed["body"] = body
                result = super().consume_post_permit(target, body)
                observed["permit_accepted"] = result
                return result
        actions = [{"id":"id:complex-send", "action":"submit"}]
        with mock.patch.object(render_browser, "_BoundedThreadingHTTPServer", RecordingServer):
            try:
                payload = render_browser.rendered_browse(
                    self.base + "/form-complex", wait_ms=100, actions=actions,
                    allow_form_post=True)
            except browser.BrowserError as error:
                self.fail(f"render failed; permit evidence={observed!r}: {error}")
        self.assertEqual(payload["page"]["url"], self.base + "/result")
        # HTML text inputs sanitize embedded line breaks; textarea values retain
        # them and URL-encoded form submission normalizes those to CRLF.
        expected = (b"dup=first&dup=second&linebreak=line+oneline+two&"
                    b"notes=line+one%0D%0Aline+two+%26+caf%C3%A9&submitter=save")
        self.assertTrue(observed.get("permit_accepted"),
                        f"permit rejected; granted_target={observed.get('granted_target')!r}, "
                        f"granted_body={observed.get('granted_body')!r}, "
                        f"received_target={observed.get('target')!r}, received_body={observed.get('body')!r}")
        self.assertEqual(observed["body"], expected)
        self.assertEqual(self.server.submitted.encode("utf-8"), expected)
        self.assertNotIn("disabled=", self.server.submitted)

    def test_303_after_form_post_is_followed_as_get_without_second_post(self):
        self.server.post_paths = []
        command = [sys.executable, "-m", "odd1_browser.cli", "render", self.base + "/post-redirect-form",
                   # Zero caller wait still needs to safely observe navigation.
                   "--wait-ms", "0", "--actions-json", json.dumps([
                       {"id":"id:redirect-send", "action":"submit"}]),
                   "--allow-form-post"]
        completed = subprocess.run(command, check=False, capture_output=True,
                                   text=True, timeout=90)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["page"]["url"], self.base + "/after-post-redirect")
        self.assertEqual(payload["page"]["title"], "Redirected")
        self.assertEqual(self.server.post_paths, ["/post-redirect"])
        with _Fixture.lock:
            self.assertIn("/after-post-redirect", _Fixture.seen)

    def test_form_post_is_denied_without_explicit_cli_authorization(self):
        with _Fixture.lock:
            _Fixture.seen.clear()
        command = [sys.executable, "-m", "odd1_browser.cli", "render", self.base + "/form", "--wait-ms", "100",
                   "--actions-json", json.dumps([{"id":"id:send", "action":"submit"}])]
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=90)
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn("allow_form_post=True caller authorization", completed.stderr)
        with _Fixture.lock:
            self.assertNotIn("/result", _Fixture.seen)

    def test_real_renderer_cli_emits_schema_json_and_screenshot(self):
        # Exercise the public subprocess interface with actual Firefox rather
        # than calling rendered_browse directly; no network beyond this fixture.
        completed = subprocess.run(
            [sys.executable, "-m", "odd1_browser.cli", "render", self.base + "/page",
             "--wait-ms", "1500", "--screenshot"],
            check=False, capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["engine"], "Firefox")
        self.assertEqual(payload["status"], 200)
        self.assertEqual(payload["page"]["title"], "after-js")
        self.assertIn("rendered by Firefox", payload["page"]["text"])
        screenshot = base64.b64decode(payload["screenshot_png_base64"], validate=True)
        self.assertTrue(screenshot.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertLessEqual(len(screenshot), render_browser.MAX_SCREENSHOT_BYTES)

    def test_external_top_level_navigation_is_presented_to_proxy_and_denied(self):
        destinations = []
        upstream_targets = []
        original_proxy = render_browser._LoopbackProxy
        original_open = render_browser._DIRECT.open

        class RecordingProxy(original_proxy):
            def do_GET(self):
                destinations.append(self.path)
                super().do_GET()

        def recording_open(request, *args, **kwargs):
            upstream_targets.append(request.full_url)
            return original_open(request, *args, **kwargs)

        with mock.patch.object(render_browser, "_LoopbackProxy", RecordingProxy), mock.patch.object(
                render_browser._DIRECT, "open", side_effect=recording_open):
            # Firefox may leave current_url at the attempted external URL even
            # after receiving the proxy's denial; either outcome must not reach
            # the upstream opener. If URL extraction rejects it, that is also
            # an expected safe failure rather than evidence of a successful page.
            try:
                render_browser.rendered_browse(self.base + "/navigate-external", wait_ms=1500)
            except browser.BrowserError:
                pass

        self.assertTrue(any("example.com/orbit-denied-top-level" in target for target in destinations),
                        destinations)
        self.assertFalse(any("example.com" in target for target in upstream_targets), upstream_targets)
        with _Fixture.lock:
            upstream_paths = list(_Fixture.seen)
        self.assertIn("/navigate-external", upstream_paths)

    def test_download_response_is_denied_before_reaching_firefox(self):
        destinations = []
        original_proxy = render_browser._LoopbackProxy

        class RecordingProxy(original_proxy):
            def do_GET(self):
                destinations.append(self.path)
                super().do_GET()

        with mock.patch.object(render_browser, "_LoopbackProxy", RecordingProxy):
            result = render_browser.rendered_browse(self.base + "/download", wait_ms=100)
        self.assertEqual(result["page"]["url"], self.base + "/download")
        self.assertTrue(any(target.endswith("/download") for target in destinations), destinations)

    def test_external_redirect_denied_before_upstream_connect(self):
        original_open = render_browser._DIRECT.open
        upstream_targets = []

        def recording_open(request, *args, **kwargs):
            upstream_targets.append(request.full_url)
            return original_open(request, *args, **kwargs)

        with mock.patch.object(render_browser._DIRECT, "open", side_effect=recording_open):
            result = render_browser.rendered_browse(self.base + "/redirect-external", wait_ms=100)
        # The validating proxy converts the rejected redirect into an HTTP 403
        # response, so Firefox stays on the original URL rather than navigating.
        self.assertEqual(result["page"]["url"], self.base + "/redirect-external")
        self.assertIn(self.base + "/redirect-external", upstream_targets)
        self.assertFalse(any("example.com" in target for target in upstream_targets), upstream_targets)

    def test_real_webdriver_service_is_group_leader_and_reaped(self):
        """Exercise the production process-group cleanup with live Firefox.

        This checks normal Selenium ownership/teardown only; the wedged-driver
        timeout remains covered by the separate controlled-process unit test.
        """
        import os
        if os.name != "posix":
            self.skipTest("POSIX process sessions are required")
        original_close = render_browser._close_driver_and_service
        observed = {}

        def observe_close(driver, service):
            process = service.process
            self.assertIsNotNone(process, "Selenium service has no owned process")
            observed["process"] = process
            observed["pid"] = process.pid
            observed["pgid"] = os.getpgid(process.pid)
            observed["sid"] = os.getsid(process.pid)
            original_close(driver, service)
            observed["returncode"] = process.poll()

        with mock.patch.object(render_browser, "_close_driver_and_service", side_effect=observe_close):
            result = render_browser.rendered_browse(self.base + "/page", wait_ms=100)
        self.assertEqual(result["page"]["title"], "after-js")
        self.assertEqual(observed["pid"], observed["pgid"], "geckodriver was not process-group leader")
        self.assertEqual(observed["pid"], observed["sid"], "geckodriver did not start a new session")
        self.assertIsNotNone(observed["returncode"], "geckodriver leader was not reaped")
        deadline = __import__("time").monotonic() + 2
        while __import__("time").monotonic() < deadline:
            try:
                os.killpg(observed["pgid"], 0)
            except ProcessLookupError:
                break
            __import__("time").sleep(0.05)
        else:
            self.fail("owned Firefox/geckodriver process group still exists after cleanup")

    def test_real_webdriver_hung_quit_terminates_process_group(self):
        """Fault-inject a hung WebDriver call while Firefox/geckodriver are live."""
        import os
        import time
        if os.name != "posix":
            self.skipTest("POSIX process sessions are required")
        original_close = render_browser._close_driver_and_service
        observed = {}
        release_quit = threading.Event()

        def force_hung_quit(driver, service):
            process = service.process
            self.assertIsNotNone(process, "Selenium service has no owned process")
            observed["process"] = process
            observed["pid"] = process.pid
            observed["pgid"] = os.getpgid(process.pid)
            observed["sid"] = os.getsid(process.pid)
            actual_quit = driver.quit

            def wedged_quit():
                release_quit.wait(30)

            driver.quit = wedged_quit
            try:
                original_close(driver, service)
            finally:
                driver.quit = actual_quit
                release_quit.set()
                observed["returncode"] = process.poll()

        with mock.patch.object(render_browser, "MAX_DRIVER_QUIT_SECONDS", 0.2), mock.patch.object(
                render_browser, "_close_driver_and_service", side_effect=force_hung_quit):
            with self.assertRaisesRegex(browser.BrowserError, "shutdown exceeded"):
                render_browser.rendered_browse(self.base + "/page", wait_ms=100)

        process = observed["process"]
        self.assertEqual(observed["pid"], observed["pgid"], "geckodriver was not group leader")
        self.assertEqual(observed["pid"], observed["sid"], "geckodriver did not own a new session")
        self.assertIsNotNone(observed["returncode"], "geckodriver leader was not reaped")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.killpg(observed["pgid"], 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("live Firefox/geckodriver process group remains after hung quit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
