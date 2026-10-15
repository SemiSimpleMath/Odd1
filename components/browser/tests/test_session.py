"""FirefoxSession and the MCP tools over it, offline: a fake WebDriver stands in for Firefox.

The real-Firefox path is covered by test_render_firefox.py (skips without Selenium and geckodriver) and by
test_mcp_live_firefox below (skips without mcp, Selenium and geckodriver).
"""
import asyncio
import http.server
import importlib.util
import json
import os
import shutil
import sys
import threading
import types
import unittest

from odd1_browser import render
from odd1_browser import text as browser

PAGE = {"title": "Form", "text": "Name", "text_truncated": False, "headings": [], "headings_truncated": False,
        "links": [], "links_truncated": False, "elements": [{"id": "el:0001", "tag": "input", "label": "",
        "disabled": False}], "elements_truncated": False, "stable_ids": [], "ready_state": "complete"}


class FakeDriver:
    """Records what the session asks of WebDriver; every page script returns canned results."""

    def __init__(self):
        self.current_url = "http://127.0.0.1:8000/"
        self.visited = []
        self.scripts = []

    def quit(self):
        self.quit_called = True

    def get(self, url):
        self.visited.append(url)
        self.current_url = url

    def execute_script(self, script, *args):
        self.scripts.append(args)
        if args and args[0] == render.MAX_RENDER_TEXT:  # the extraction script
            return dict(PAGE)
        return {"ok": True, "tag": "input"}


def fake_session():
    session = render.FirefoxSession()
    driver = FakeDriver()

    def start():
        session.driver = driver
        if session.proxy_server is None:
            session.proxy_server = types.SimpleNamespace(shutdown=lambda: None, server_close=lambda: None)
            session.proxy_thread = types.SimpleNamespace(join=lambda timeout: None)
    session._start = start
    return session, driver


class SessionTests(unittest.TestCase):
    def test_open_view_act_keep_one_browser(self):
        session, driver = fake_session()
        view = session.open("http://127.0.0.1:8000/", wait_ms=0)
        self.assertEqual(view["engine"], "Firefox")
        self.assertEqual(view["page"]["elements"][0]["id"], "el:0001")
        self.assertEqual(session.proxy_server.allowed_post_origin, ("http", "127.0.0.1", 8000))
        after = session.act([{"id": "el:0001", "action": "type", "value": "Ada"}], wait_ms=0)
        self.assertEqual(after["actions"], [{"id": "el:0001", "action": "type", "tag": "input"}])
        self.assertEqual(driver.visited, ["http://127.0.0.1:8000/"])
        self.assertIn(("el:0001", "type", "Ada"), driver.scripts)

    def test_view_and_act_need_an_open_page(self):
        session = render.FirefoxSession()
        with self.assertRaisesRegex(browser.BrowserError, "no page is open"):
            session.view()
        with self.assertRaisesRegex(browser.BrowserError, "no page is open"):
            session.act([{"id": "el:0001", "action": "click"}])
        session.close()  # nothing started: a no-op
        session.close()

    def test_open_refuses_non_loopback_before_starting_firefox(self):
        session = render.FirefoxSession()
        with self.assertRaisesRegex(browser.BrowserError, "loopback"):
            session.open("http://example.com/")
        self.assertIsNone(session.proxy_server)

    def test_driver_failures_are_reported_as_browser_errors(self):
        session, driver = fake_session()
        session.open("http://127.0.0.1:8000/", wait_ms=0)
        driver.execute_script = lambda *_: (_ for _ in ()).throw(RuntimeError("tab crashed"))
        with self.assertRaisesRegex(browser.BrowserError, "tab crashed"):
            session.view()

    def test_close_reaps_driver_and_proxy_once(self):
        session = render.FirefoxSession()
        calls = []
        session.driver, session.service = "driver", "service"
        session.proxy_server, session.proxy_thread = "proxy", "thread"
        original = render._close_driver_and_service, render._close_runtime
        render._close_driver_and_service = lambda d, s: calls.append(("driver", d, s))
        render._close_runtime = lambda d, p, t: calls.append(("proxy", p, t))
        try:
            session.close()
            session.close()
        finally:
            render._close_driver_and_service, render._close_runtime = original
        self.assertEqual(calls, [("driver", "driver", "service"), ("proxy", "proxy", "thread")])
        self.assertIsNone(session.driver)


@unittest.skipUnless(importlib.util.find_spec("mcp"), "the mcp package is not installed (module.nix provides it)")
class McpToolTests(unittest.TestCase):
    def setUp(self):
        from odd1_browser import mcp_server
        self.server = mcp_server
        self.session, self.driver = fake_session()
        mcp_server._session = self.session

    def tearDown(self):
        self.server._session = None

    def test_tools_are_registered_with_odd1_names(self):
        names = {tool.name for tool in asyncio.run(self.server.mcp.list_tools())}
        self.assertEqual(names, {"browser_open", "browser_view", "browser_click", "browser_type", "browser_select",
                                 "browser_submit", "browser_find", "browser_fetch", "browser_close"})

    def test_tools_drive_the_session(self):
        view = self.server.browser_open("http://127.0.0.1:8000/", wait_ms=0)
        self.assertEqual(view["page"]["title"], "Form")
        typed = self.server.browser_type("el:0001", "Ada")
        self.assertEqual(typed["actions"][0]["action"], "type")
        found = self.server.browser_find("name")
        self.assertEqual(found["matches"][0]["start"], 0)
        self.assertEqual(self.server.browser_close(), "closed")
        self.assertIsNone(self.server._session)
        self.assertTrue(self.driver.quit_called)


class _Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = (b"<title>t</title><p id='out'>waiting</p><input id='name'>"
                b"<button id='go' onclick=\"document.getElementById('out').textContent='hello '+"
                b"document.getElementById('name').value\">Go</button>")  # not `name`: that is window.name
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@unittest.skipUnless(importlib.util.find_spec("mcp") and importlib.util.find_spec("selenium")
                     and shutil.which("geckodriver"), "needs mcp, Selenium and geckodriver (Odd1 Desktop)")
class McpLiveFirefoxTests(unittest.TestCase):
    def test_mcp_live_firefox(self):
        """A real MCP client opens a JS page, types, clicks, and reads the script's result."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}/"

        async def run():
            params = StdioServerParameters(command=sys.executable, args=["-m", "odd1_browser.mcp_server"],
                                           env=dict(os.environ))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()

                    async def call(name, args):
                        result = await client.call_tool(name, args)
                        return json.loads(result.content[0].text)
                    await call("browser_open", {"url": url})
                    await call("browser_type", {"id": "id:name", "text": "Ada"})
                    view = await call("browser_click", {"id": "id:go"})
                    await client.call_tool("browser_close", {})
                    return view
        try:
            view = asyncio.run(run())
        finally:
            server.shutdown()
            server.server_close()
        self.assertIn("hello Ada", view["page"]["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
