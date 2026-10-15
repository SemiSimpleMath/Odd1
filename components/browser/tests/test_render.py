"""Offline security/integration tests for optional Firefox renderer proxy."""
import base64
import http.server
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from odd1_browser import text as browser
from odd1_browser import render as render_browser


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/download":
            body = b"must-not-be-downloaded"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="payload.bin"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/form":
            body = (b"<!doctype html><title>Form</title><h1>Feedback</h1>"
                    b"<form id='feedback' method='post' action='/result'>"
                    b"<label>Name <input id='name' name='name' type='text'></label>"
                    b"<label>Topic <select id='topic' name='topic'><option value='a'>Alpha</option><option value='b'>Beta</option></select></label>"
                    b"<button id='send' type='submit'>Send</button></form>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/result":
            body = b"<!doctype html><title>Submitted</title><h1>Thank you</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/redirect-external":
            self.send_response(302)
            self.send_header("Location", "http://example.com/should-not-follow")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/redirect-safe":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/final":
            body = b"redirect-followed"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


    def do_POST(self):
        if self.path == "/post-redirect-external":
            self.server.post_redirect_seen = True
            self.send_response(303)
            self.send_header("Location", "http://example.com/should-not-follow-post")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        form = self.rfile.read(length).decode("utf-8")
        self.server.submitted = form
        self.server.submission_count = getattr(self.server, "submission_count", 0) + 1
        body = b"<!doctype html><title>Submitted</title><h1>Thank you</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RenderProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.upstream.server_port}/"
        cls.origin = ("http", "127.0.0.1", cls.upstream.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def setUp(self):
        self.proxy = render_browser._BoundedThreadingHTTPServer(("127.0.0.1", 0), render_browser._LoopbackProxy)
        self.proxy.allowed_post_origin = self.origin
        self.proxy.grant_post_permit(self.url + "result", b"name=Ada&topic=b")
        self.thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.proxy.shutdown()
        self.proxy.server_close()
        self.thread.join(timeout=2)

    def test_proxy_production_worker_limit_rejects_excess_and_recovers(self):
        import socket

        lifecycle = []
        lifecycle_lock = threading.Lock()

        def record(event, request, peer, **details):
            # Keep diagnostics connection-scoped and small; make assertion
            # failures explain whether dispatch, worker completion, or release
            # went missing rather than reporting only a semaphore snapshot.
            with lifecycle_lock:
                lifecycle.append({
                    "event": event,
                    "peer": peer,
                    "socket": request.fileno(),
                    "thread": threading.current_thread().name,
                    "slots": server._worker_slots._value,
                    **details,
                })

        class ShortTimeoutProxy(render_browser._LoopbackProxy):
            # Keep this test bounded while giving all parallel sockets time to start.
            timeout_seconds = 2

        class LifecycleServer(render_browser._BoundedThreadingHTTPServer):
            def process_request(self, request, client_address):
                record("accepted", request, client_address)
                try:
                    super().process_request(request, client_address)
                except BaseException as exc:
                    record("dispatch-error", request, client_address, error=repr(exc))
                    raise
                record("dispatched", request, client_address)

            def process_request_thread(self, request, client_address):
                record("worker-start", request, client_address)
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    record("worker-finished-and-slot-released", request, client_address)

        server = LifecycleServer(
            ("127.0.0.1", 0), ShortTimeoutProxy
        )
        diagnostic = lambda: f"; lifecycle={lifecycle!r}"
        # Match the fixture setup of RenderProxyTests without creating a real
        # proxy listener for this isolated saturation server.
        server.grant_post_permit = lambda _target, _body: None
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        clients = []
        excess = None
        try:
            # Occupy every configured production worker with an incomplete request.
            for _ in range(server.max_workers):
                clients.append(socket.create_connection(("127.0.0.1", server.server_port), timeout=2))
            deadline = time.monotonic() + 2
            while server._worker_slots._value != 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(
                server._worker_slots._value, 0,
                f"did not occupy all {server.max_workers} production workers{diagnostic()}",
            )
            excess = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
            excess.settimeout(2)
            response = excess.recv(1024)
            self.assertIn(b" 503 ", response, response)
            self.assertEqual(server._worker_slots._value, 0, f"rejected socket consumed a worker{diagnostic()}")
            # Once incomplete requests time out, all production worker slots return.
            deadline = time.monotonic() + 3
            while server._worker_slots._value != server.max_workers and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(
                server._worker_slots._value, server.max_workers,
                f"stalled requests retained production worker slots after timeout{diagnostic()}",
            )
        finally:
            for client in clients:
                client.close()
            if excess:
                excess.close()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)
        self.assertFalse(server_thread.is_alive(), "proxy accept loop did not stop")

    def test_proxy_denies_attachment_download(self):
        import socket
        with socket.create_connection(("127.0.0.1", self.proxy.server_port), timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall((f"GET {self.url}download HTTP/1.1\r\n"
                          f"Host: 127.0.0.1:{self.upstream.server_port}\r\n"
                          "Connection: close\r\n\r\n").encode())
            chunks = []
            while True:
                chunk = sock.recv(2048)
                if not chunk:
                    break
                chunks.append(chunk)
            response = b"".join(chunks)
        self.assertIn(b" 403 ", response)
        self.assertIn(b"Downloads are not supported", response)

    def test_proxy_fetches_loopback_directly(self):
        import urllib.request
        proxy = f"http://127.0.0.1:{self.proxy.server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy}))
        with opener.open(self.url, timeout=2) as response:
            self.assertEqual(response.read(), b"ok")

    def test_proxy_rejects_external_target_and_connect(self):
        import urllib.request
        proxy = f"http://127.0.0.1:{self.proxy.server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy}))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open("http://example.com/", timeout=2)
        self.assertEqual(caught.exception.code, 403)

    def test_proxy_refuses_https_absolute_target_before_upstream(self):
        import socket
        original_open = render_browser._DIRECT.open
        upstream = []
        def recording_open(request, *args, **kwargs):
            upstream.append(request.full_url)
            return original_open(request, *args, **kwargs)
        with mock.patch.object(render_browser._DIRECT, "open", side_effect=recording_open):
            with socket.create_connection(("127.0.0.1", self.proxy.server_port), timeout=2) as sock:
                sock.sendall(b"GET https://example.com/ HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n")
                response = sock.recv(1024)
        self.assertIn(b" 403 ", response)
        self.assertEqual(upstream, [])

    def test_safe_redirect_is_revalidated_and_followable(self):
        import urllib.request
        proxy = f"http://127.0.0.1:{self.proxy.server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy}))
        with opener.open(self.url + "redirect-safe", timeout=2) as response:
            self.assertEqual(response.read(), b"redirect-followed")

    def test_external_redirect_is_not_followed_by_proxy_upstream(self):
        import urllib.request
        proxy = f"http://127.0.0.1:{self.proxy.server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy}))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(self.url + "redirect-external", timeout=2)
        # urllib follows the 302 through the proxy; its next external request
        # is refused by policy rather than reaching the external destination.
        self.assertEqual(caught.exception.code, 403)

    def test_non_form_post_rejected_without_upstream_request(self):
        import socket
        self.proxy.grant_post_permit(self.url, b"name=Ada&topic=b")
        with socket.create_connection(("127.0.0.1", self.proxy.server_port), timeout=2) as sock:
            sock.settimeout(2)
            request = (f"POST {self.url} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                       "Content-Type: application/json\r\nContent-Length: 1\r\nConnection: close\r\n\r\nx").encode()
            sock.sendall(request)
            response = sock.recv(1024)
        self.assertIn(b" 415 ", response)

    def test_connect_tunnel_is_rejected(self):
        import socket
        with socket.create_connection(("127.0.0.1", self.proxy.server_port), timeout=2) as sock:
            sock.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
            response = sock.recv(1024)
        self.assertTrue(response.startswith(b"HTTP/1.1 405") or response.startswith(b"HTTP/1.0 405"), response)

    def test_extraction_uses_rendered_bounded_dom(self):
        class FakeDriver:
            current_url = self.url
            def execute_script(self, *_):
                return {"title": "rendered", "text": "JS result", "text_truncated": False,
                        "headings": [], "headings_truncated": False, "links": [],
                        "links_truncated": False, "elements": [{"id":"el:0001"}],
                        "elements_truncated": False, "stable_ids": [], "ready_state": "complete"}
        page = render_browser._extract_driver(FakeDriver(), self.url)
        self.assertEqual(page["title"], "rendered")
        self.assertEqual(page["text"], "JS result")
        self.assertEqual(page["url"], self.url)
        self.assertEqual(page["elements"][0]["id"], "el:0001")

    def test_action_validation_rejects_unbounded_or_unsafe_actions(self):
        invalid_actions = [
            {"id":"el:0001", "action":"execute"},
            {"id":"el:0001", "action":"type", "value":"x" * (render_browser._MAX_ACTION_VALUE + 1)},
            {"id":"el:0001", "action":"click", "value":"unexpected"},
            {"id":"bad id", "action":"click"},
        ]
        for action in invalid_actions:
            with self.subTest(action=action):
                with self.assertRaises(browser.BrowserError):
                    render_browser._validate_actions([action])
        with self.assertRaises(browser.BrowserError):
            render_browser._validate_actions([{"id":"el:0001", "action":"click"}] * (render_browser._MAX_ACTIONS + 1))

    def test_form_post_requires_explicit_caller_authorization(self):
        target = self.url + "result"
        class FakeDriver:
            def __init__(self):
                self.calls = 0
            def execute_script(self, *_args):
                self.calls += 1
                if self.calls == 1:
                    return {"target": target, "body": "name=Ada"}
                return {"ok": True, "tag": "button"}

        driver = FakeDriver()
        self.proxy._post_permit = None
        with self.assertRaisesRegex(browser.BrowserError, "allow_form_post=True"):
            render_browser._perform_actions(
                driver, [{"id": "id:send", "action": "submit", "value": ""}], self.proxy
            )
        # Authorization is checked before issuing the mutating DOM action.
        self.assertEqual(driver.calls, 1)
        self.assertIsNone(self.proxy._post_permit)

    def test_proxy_bounds_and_forwards_urlencoded_post(self):
        import socket
        form = b"name=Ada&topic=b"
        with socket.create_connection(("127.0.0.1", self.proxy.server_port), timeout=2) as sock:
            sock.settimeout(2)
            headers = (f"POST {self.url}result HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                       f"Content-Type: application/x-www-form-urlencoded\r\nContent-Length: {len(form)}\r\n"
                       "Connection: close\r\n\r\n").encode()
            sock.sendall(headers + form)
            response = b""
            while True:
                chunk = sock.recv(2048)
                if not chunk: break
                response += chunk
        self.assertIn(b" 200 ", response)
        self.assertIn(b"Thank you", response)
        self.assertEqual(self.upstream.submitted, form.decode())

    def test_post_permit_is_bound_to_exact_destination_and_one_use(self):
        import socket
        self.proxy.grant_post_permit(self.url + "result", b"name=Ada&topic=b")
        before_submissions = getattr(self.upstream, "submission_count", 0)
        def post(path):
            body = b"name=Ada&topic=b"
            with socket.create_connection(("127.0.0.1", self.proxy.server_port), timeout=2) as sock:
                sock.settimeout(2)
                request = (f"POST {self.url}{path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                           f"Content-Type: application/x-www-form-urlencoded\r\nContent-Length: {len(body)}\r\n"
                           "Connection: close\r\n\r\n").encode() + body
                sock.sendall(request)
                return sock.recv(1024)
        # A different destination and a changed body must not consume the permit.
        self.assertIn(b" 403 ", post("other"))
        with socket.create_connection(("127.0.0.1", self.proxy.server_port), timeout=2) as sock:
            sock.settimeout(2)
            changed = b"name=Eve&topic=b"
            request = (f"POST {self.url}result HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                       f"Content-Type: application/x-www-form-urlencoded\r\nContent-Length: {len(changed)}\r\n"
                       "Connection: close\r\n\r\n").encode() + changed
            sock.sendall(request)
            self.assertIn(b" 403 ", sock.recv(1024))
        # The one authorized URL can use it exactly once.
        self.assertIn(b" 200 ", post("result"))
        self.assertIn(b" 403 ", post("result"))
        self.assertEqual(getattr(self.upstream, "submission_count", 0) - before_submissions, 1)

    def test_post_permit_is_single_use_and_expires(self):
        self.proxy.grant_post_permit(self.url + "result", b"name=Ada&topic=b")
        self.assertTrue(self.proxy.consume_post_permit(self.url + "result", b"name=Ada&topic=b"))
        self.assertFalse(self.proxy.consume_post_permit(self.url + "result", b"name=changed"))
        self.assertFalse(self.proxy.consume_post_permit(self.url + "result", b"name=Ada&topic=b"))
        self.proxy.grant_post_permit(self.url + "result", b"name=Ada&topic=b")
        with mock.patch.object(render_browser.time, "monotonic", return_value=time.monotonic() + 2):
            self.assertFalse(self.proxy.consume_post_permit(self.url + "result", b"name=Ada&topic=b"))

    def test_post_permit_cannot_be_granted_cross_origin(self):
        with self.assertRaises(browser.BrowserError):
            self.proxy.grant_post_permit("http://example.com/submit", b"x=y")

    def test_proxy_rejects_cross_origin_form_post(self):
        import socket
        original = self.proxy.allowed_post_origin
        self.proxy.allowed_post_origin = ("http", "127.0.0.1", self.upstream.server_port + 1)
        self.proxy._post_permit = None
        try:
            with socket.create_connection(("127.0.0.1", self.proxy.server_port), timeout=2) as sock:
                sock.settimeout(2)
                body = b"name=Ada"
                request = (f"POST {self.url}result HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                           f"Content-Type: application/x-www-form-urlencoded\r\nContent-Length: {len(body)}\r\n"
                           "Connection: close\r\n\r\n").encode() + body
                sock.sendall(request)
                response = sock.recv(1024)
            self.assertIn(b" 403 ", response)
        finally:
            self.proxy.allowed_post_origin = original

    def test_proxy_rejects_oversized_form_body(self):
        import socket
        self.proxy.grant_post_permit(self.url, b"name=Ada&topic=b")
        with socket.create_connection(("127.0.0.1", self.proxy.server_port), timeout=2) as sock:
            sock.settimeout(2)
            headers = (f"POST {self.url} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                       "Content-Type: application/x-www-form-urlencoded\r\nContent-Length: 16385\r\n\r\n").encode()
            sock.sendall(headers)
            response = sock.recv(1024)
        self.assertIn(b" 413 ", response)

    def test_proxy_rejects_ambiguous_post_framing(self):
        import socket
        requests = [
            # Even identical duplicate lengths are rejected rather than relying
            # on differing HTTP parser/proxy interpretations.
            (b"Content-Length: 8\r\nContent-Length: 8\r\n", b"name=Ada", b" 400 "),
            (b"Transfer-Encoding: chunked\r\nContent-Length: 8\r\n",
             b"8\r\nname=Ada\r\n0\r\n\r\n", b" 400 "),
        ]
        submissions = getattr(self.upstream, "submission_count", 0)
        for framing, body, expected_status in requests:
            with self.subTest(framing=framing), socket.create_connection(
                    ("127.0.0.1", self.proxy.server_port), timeout=2) as sock:
                sock.settimeout(2)
                headers = (f"POST {self.url}result HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                           "Content-Type: application/x-www-form-urlencoded\r\n").encode()
                sock.sendall(headers + framing + b"Connection: close\r\n\r\n" + body)
                response = sock.recv(1024)
                self.assertIn(expected_status, response)
        self.assertEqual(getattr(self.upstream, "submission_count", 0), submissions)

    def test_screenshot_base64_size_limit(self):
        class FakeDriver:
            current_url = "http://127.0.0.1/"
            def get_screenshot_as_base64(self):
                return base64.b64encode(b"png data").decode("ascii")
        encoded = render_browser._bounded_screenshot(FakeDriver())
        self.assertEqual(base64.b64decode(encoded, validate=True), b"png data")
        class OversizedDriver:
            def get_screenshot_as_base64(self):
                return base64.b64encode(b"x" * (render_browser.MAX_SCREENSHOT_BYTES + 1)).decode("ascii")
        with self.assertRaisesRegex(browser.BrowserError, "screenshot exceeds"):
            render_browser._bounded_screenshot(OversizedDriver())
        class InvalidDriver:
            def get_screenshot_as_base64(self):
                return "not-base64!"
        with self.assertRaisesRegex(browser.BrowserError, "invalid screenshot"):
            render_browser._bounded_screenshot(InvalidDriver())

    def test_firefox_configuration_disables_external_protocol_handlers(self):
        class FakeOptions:
            def __init__(self):
                self.args = []
                self.prefs = {}
            def add_argument(self, value):
                self.args.append(value)
            def set_preference(self, name, value):
                self.prefs[name] = value

        options = FakeOptions()
        render_browser._configure_firefox_options(options, 45678, headless=True)
        self.assertIn("-headless", options.args)
        self.assertEqual(options.prefs["network.proxy.http_port"], 45678)
        self.assertEqual(options.prefs["network.proxy.ssl_port"], 45678)
        self.assertEqual(options.prefs["network.protocol-handler.external-default"], False)
        self.assertEqual(options.prefs["network.protocol-handler.warn-external-default"], False)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups are required")
    def test_process_group_cleanup_signals_driver_and_descendant(self):
        # Exercise the real POSIX process-group operation without launching Firefox.
        with tempfile.TemporaryDirectory() as directory:
            ready = os.path.join(directory, "child-ready")
            parent_mark = os.path.join(directory, "parent-term")
            child_mark = os.path.join(directory, "child-term")
            child_code = (
                "import signal,time; "
                f"signal.signal(signal.SIGTERM, lambda *_: (open({child_mark!r},'a').write('term'), exit(0))); "
                f"open({child_mark!r},'w').close(); "
                "time.sleep(60)"
            )
            parent_code = (
                "import signal,subprocess,sys,time; "
                f"signal.signal(signal.SIGTERM, lambda *_: (open({parent_mark!r},'w').write('term'), exit(0))); "
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
                "time.sleep(60)"
            )
            process = subprocess.Popen([sys.executable, "-c", parent_code], start_new_session=True)
            try:
                # Find the child marker without relying on process listing tools.
                deadline = time.monotonic() + 3
                while not os.path.exists(child_mark) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(os.path.exists(child_mark), "descendant failed to start")
                render_browser._terminate_process_group(process, grace_seconds=1)
                self.assertIsNotNone(process.poll(), "group leader was not reaped")
                with open(parent_mark) as marker:
                    self.assertIn("term", marker.read())
                # SIGTERM delivery and the child handler may race reaping; wait
                # briefly for its marker instead of asserting immediately.
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        with open(child_mark) as marker:
                            if "term" in marker.read():
                                break
                    except FileNotFoundError:
                        pass
                    time.sleep(0.01)
                with open(child_mark) as marker:
                    self.assertIn("term", marker.read())
            finally:
                if process.poll() is None:
                    render_browser._terminate_process_group(process, grace_seconds=0.2)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups are required")
    def test_bounded_webdriver_shutdown_terminates_owned_process_tree(self):
        # Exercise the real cleanup orchestration with a controlled process tree
        # and a simulated wedged WebDriver quit call (never launch Firefox).
        with tempfile.TemporaryDirectory() as directory:
            ready = os.path.join(directory, "child-ready")
            parent_mark = os.path.join(directory, "parent-term")
            child_mark = os.path.join(directory, "child-term")
            child_code = (
                "import signal,time; "
                f"signal.signal(signal.SIGTERM, lambda *_: (open({child_mark!r},'w').write('term'), exit(0))); "
                f"open({ready!r},'w').close(); time.sleep(60)"
            )
            parent_code = (
                "import signal,subprocess,sys,time; "
                f"signal.signal(signal.SIGTERM, lambda *_: (open({parent_mark!r},'w').write('term'), exit(0))); "
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); time.sleep(60)"
            )
            process = subprocess.Popen([sys.executable, "-c", parent_code], start_new_session=True)
            released = threading.Event()

            class WedgedDriver:
                def quit(self):
                    released.wait(30)

            class Service:
                pass
            service = Service()
            service.process = process
            try:
                deadline = time.monotonic() + 3
                while not os.path.exists(ready) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(os.path.exists(ready), "descendant failed to start")
                with self.assertRaisesRegex(browser.BrowserError, "shutdown exceeded"):
                    render_browser._close_driver_and_service(WedgedDriver(), service)
                self.assertIsNotNone(process.poll(), "WebDriver process group leader was not reaped")
                self.assertTrue(os.path.exists(parent_mark), "group leader did not receive SIGTERM")
                self.assertTrue(os.path.exists(child_mark), "descendant did not receive SIGTERM")
            finally:
                released.set()
                if process.poll() is None:
                    render_browser._terminate_process_group(process, grace_seconds=0.2)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups are required")
    def test_process_group_cleanup_escalates_to_sigkill(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = os.path.join(directory, "ready")
            process = subprocess.Popen(
                [sys.executable, "-c",
                 "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                 f"open({ready!r},'w').close(); time.sleep(60)"],
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 3
                while not os.path.exists(ready) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(os.path.exists(ready), "process failed to start")
                render_browser._terminate_process_group(process, grace_seconds=0.1)
                self.assertIsNotNone(process.poll(), "SIGKILL escalation did not reap the leader")
                self.assertEqual(process.returncode, -9)
            finally:
                if process.poll() is None:
                    render_browser._terminate_process_group(process, grace_seconds=0.1)

    def test_runtime_cleanup_attempts_proxy_shutdown_when_driver_quit_fails(self):
        events = []

        class BadDriver:
            def quit(self):
                events.append("quit")
                raise RuntimeError("driver shutdown failed")

        class Proxy:
            def shutdown(self):
                events.append("shutdown")
            def server_close(self):
                events.append("server_close")

        thread = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "driver shutdown failed"):
            render_browser._close_runtime(BadDriver(), Proxy(), thread)
        self.assertEqual(events, ["quit", "shutdown", "server_close"])
        thread.join.assert_called_once_with(timeout=2)

    def test_bad_render_params_rejected_before_starting_browser(self):
        with self.assertRaisesRegex(browser.BrowserError, "wait_ms"):
            render_browser.rendered_browse(self.url, wait_ms=render_browser.MAX_WAIT_MS + 1)
        with self.assertRaisesRegex(browser.BrowserError, "allow_form_post"):
            render_browser.rendered_browse(self.url, allow_form_post="yes")


if __name__ == "__main__":
    unittest.main(verbosity=2)
