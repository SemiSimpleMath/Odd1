#!/usr/bin/env python3
"""Firefox page view for agents: render a JS site and act on it by element id.

Requires Selenium and geckodriver at runtime. All HTTP(S) requests made by Firefox
are forced through a local validating proxy; only literal-loopback HTTP GETs pass,
plus one-use same-origin URL-encoded form POSTs the caller explicitly authorized.
The proxy is per session, bound to localhost, and never forwards arbitrary hosts.

    FirefoxSession: one live Firefox; open(url), view(), act(actions), close().
    rendered_browse(url, actions=...): one-shot open, act, view, close.
    python -m odd1_browser.render URL [--actions-json ...]   the one-shot CLI
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.server
import json
import os
import re
import shutil
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import text as browser

MAX_RENDER_TEXT = 100_000
MAX_WAIT_MS = 10_000
MAX_SCREENSHOT_BYTES = 4_000_000
MAX_DRIVER_QUIT_SECONDS = 10
MAX_PROXY_WORKERS = 16


class _DenyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}), _DenyRedirect())


class _BoundedThreadingHTTPServer(http.server.ThreadingHTTPServer):
    """Threaded proxy server with a hard cap on concurrent connection workers."""
    max_workers = MAX_PROXY_WORKERS
    # Match the pending-connection queue to worker capacity; HTTPServer defaults
    # to a backlog of five, which silently undercuts a larger worker pool.
    request_queue_size = MAX_PROXY_WORKERS
    daemon_threads = True
    block_on_close = False

    def __init__(self, server_address: Any, handler_class: Any):
        self._worker_slots = threading.BoundedSemaphore(self.max_workers)
        self._post_permit_lock = threading.Lock()
        self._post_permit: tuple[str, str, float] | None = None
        super().__init__(server_address, handler_class)

    def grant_post_permit(self, target: str, expected_body: bytes) -> None:
        """Permit one exact same-origin POST with the exact form data shown."""
        target = browser.validate_url(target)
        if not isinstance(expected_body, bytes) or len(expected_body) > 16_384:
            raise browser.BrowserError("form permit body must be bounded bytes")
        body_digest = hashlib.sha256(expected_body).hexdigest()
        parsed = urllib.parse.urlsplit(target)
        origin = (parsed.scheme, parsed.hostname, parsed.port or 80)
        if origin != getattr(self, "allowed_post_origin", None):
            raise browser.BrowserError("form permit target is not the initial page origin")
        with self._post_permit_lock:
            self._post_permit = (target, body_digest, time.monotonic() + 1.0)

    def consume_post_permit(self, target: str, body: bytes) -> bool:
        with self._post_permit_lock:
            if self._post_permit is None:
                return False
            permitted_target, permitted_digest, expiry = self._post_permit
            if time.monotonic() > expiry:
                self._post_permit = None
                return False
            if target != permitted_target or not isinstance(body, bytes):
                return False
            if not hmac.compare_digest(
                    permitted_digest, hashlib.sha256(body).hexdigest()):
                return False
            self._post_permit = None
            return True

    def process_request(self, request: Any, client_address: Any) -> None:
        # Never create an unbounded thread queue: reject excess accepted sockets.
        if not self._worker_slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Proxy busy\r\nConnection: close\r\n"
                    b"Content-Length: 0\r\n\r\n"
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


class _LoopbackProxy(http.server.BaseHTTPRequestHandler):
    """Tiny constrained HTTP forward proxy: validate every target before connect."""
    protocol_version = "HTTP/1.1"
    max_bytes = browser.MAX_BYTES
    timeout_seconds = browser.TIMEOUT

    def parse_request(self) -> bool:
        valid = super().parse_request()
        # BaseHTTPRequestHandler re-enables keep-alive while parsing HTTP/1.1.
        # This proxy intentionally processes only one request per connection.
        self.close_connection = True
        return valid

    def setup(self) -> None:
        super().setup()
        # This proxy handles exactly one request per client connection. Besides
        # simplifying permit lifetime, closing after one request prevents
        # unread/body framing bytes from being reinterpreted as a second request.
        self.close_connection = True
        # A client that opens a socket then stalls must not hold a worker forever.
        self.connection.settimeout(self.timeout_seconds)

    def do_GET(self) -> None:
        try:
            target = browser.validate_url(self.path)
            if any(name.lower() == "transfer-encoding" for name in self.headers):
                self.send_error(400, "Transfer-Encoding is unsupported")
                return
            request = urllib.request.Request(target, headers={
                "User-Agent": "odd1-browser-render/0.1",
                "Accept": self.headers.get("Accept", "*/*"),
            }, method="GET")
            try:
                response = _DIRECT.open(request, timeout=self.timeout_seconds)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status = response.getcode()
                # Never turn an upstream attachment response into a Firefox
                # download. Downloads are outside this read-only browser API.
                disposition = response.headers.get("Content-Disposition", "")
                if disposition.split(";", 1)[0].strip().lower() == "attachment":
                    self.send_error(403, "Downloads are not supported")
                    return
                location = response.headers.get("Location")
                safe_location = None
                if location and 300 <= status < 400:
                    # Validate before asking Firefox to follow any upstream redirect.
                    safe_location = browser.validate_url(urllib.parse.urljoin(target, location))
                body = response.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    self.send_error(502, "Upstream response exceeds byte limit")
                    return
                self.send_response(status)
                for name in ("Content-Type", "Content-Language", "Cache-Control", "Last-Modified", "ETag"):
                    value = response.headers.get(name)
                    if value:
                        self.send_header(name, value)
                if safe_location is not None:
                    self.send_header("Location", safe_location)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
        except browser.BrowserError:
            self.send_error(403, "Odd1 browser policy refused this destination")
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            self.send_error(502, f"Loopback fetch failed: {error}")

    def do_POST(self) -> None:
        """Forward only bounded URL-encoded form submissions to loopback HTTP."""
        try:
            target = browser.validate_url(self.path)
            allowed_origin = getattr(self.server, "allowed_post_origin", None)
            parsed_target = urllib.parse.urlsplit(target)
            target_origin = (parsed_target.scheme, parsed_target.hostname, parsed_target.port or 80)
            if allowed_origin is None or target_origin != allowed_origin:
                self.send_error(403, "Form submissions are restricted to the initial page origin")
                return
            if any(name.lower() == "transfer-encoding" for name in self.headers):
                self.send_error(400, "Transfer-Encoding is unsupported")
                return
            content_lengths = self.headers.get_all("Content-Length", [])
            if len(content_lengths) != 1 or not re.fullmatch(r"[0-9]+", content_lengths[0].strip()):
                self.send_error(400, "Exactly one valid Content-Length is required")
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/x-www-form-urlencoded":
                self.send_error(415, "Only URL-encoded form submissions are supported")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self.send_error(411, "Content-Length required")
                return
            if not 0 <= length <= 16_384:
                self.send_error(413, "Form body exceeds 16384 byte limit")
                return
            body = self.rfile.read(length)
            if len(body) != length:
                self.send_error(400, "Incomplete form body")
                return
            if not hasattr(self.server, "consume_post_permit") or not self.server.consume_post_permit(target, body):
                self.send_error(403, "Form POST requires a recent agent action for this exact destination and body")
                return
            request = urllib.request.Request(target, data=body, headers={
                "User-Agent": "odd1-browser-render/0.1",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": self.headers.get("Accept", "*/*"),
            }, method="POST")
            try:
                response = _DIRECT.open(request, timeout=self.timeout_seconds)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status = response.getcode()
                disposition = response.headers.get("Content-Disposition", "")
                if disposition.split(";", 1)[0].strip().lower() == "attachment":
                    self.send_error(403, "Downloads are not supported")
                    return
                location = response.headers.get("Location")
                safe_location = None
                if location and 300 <= status < 400:
                    safe_location = browser.validate_url(urllib.parse.urljoin(target, location))
                response_body = response.read(self.max_bytes + 1)
                if len(response_body) > self.max_bytes:
                    self.send_error(502, "Upstream response exceeds byte limit")
                    return
                self.send_response(status)
                for name in ("Content-Type", "Content-Language", "Cache-Control", "Last-Modified", "ETag"):
                    value = response.headers.get(name)
                    if value:
                        self.send_header(name, value)
                if safe_location is not None:
                    self.send_header("Location", safe_location)
                self.send_header("Content-Length", str(len(response_body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(response_body)
        except browser.BrowserError:
            self.send_error(403, "Odd1 browser policy refused this destination")
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            self.send_error(502, f"Loopback form submission failed: {error}")

    def do_CONNECT(self) -> None:
        self.send_error(405, "CONNECT tunnels are not supported")

    def do_PUT(self) -> None:
        self.send_error(405, "Only GET and URL-encoded form POST are supported")

    def do_PATCH(self) -> None:
        self.do_PUT()

    def do_DELETE(self) -> None:
        self.do_PUT()

    def do_OPTIONS(self) -> None:
        self.do_PUT()

    def do_HEAD(self) -> None:
        self.do_PUT()

    def log_message(self, fmt: str, *args: Any) -> None:
        # Avoid page content/URL noise in the agent contract.
        return


_ACTION_ID_RE = re.compile(r"^(?:el:[0-9]{4}|id:[A-Za-z][A-Za-z0-9_.:-]{0,127})$")
_MAX_ACTIONS = 32
_MAX_ACTION_VALUE = 4096
# Shared by every page script: which elements are interactive, and their element ids. An id ("el:0001") is given
# on first sight in document order and kept for that element while the page lives (a WeakMap on the window), so
# ids an agent read in a view stay valid for its next action on the same page.
_ELEMENT_IDS_JS = """
  const selector = 'a[href],button,input,select,textarea,[role="button"],[role="link"],form';
  window.__odd1ElementIds ||= new WeakMap();
  window.__odd1NextElementId ||= 1;
  const idFor = node => {
    let id = window.__odd1ElementIds.get(node);
    if (!id) { id = 'el:' + String(window.__odd1NextElementId++).padStart(4, '0'); window.__odd1ElementIds.set(node, id); }
    return id;
  };
"""


def _validate_actions(actions: Any) -> list[dict[str, str]]:
    """Validate a bounded, declarative sequence; page data is never executable JS."""
    if not isinstance(actions, list) or len(actions) > _MAX_ACTIONS:
        raise browser.BrowserError(f"actions must be a list of at most {_MAX_ACTIONS} items")
    result = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict) or set(action) - {"id", "action", "value"}:
            raise browser.BrowserError(f"action {index} must contain only id, action, and optional value")
        element_id, kind = action.get("id"), action.get("action")
        if not isinstance(element_id, str) or not _ACTION_ID_RE.fullmatch(element_id):
            raise browser.BrowserError(f"action {index} has invalid element id")
        if kind not in ("click", "type", "select", "submit"):
            raise browser.BrowserError(f"action {index} has unsupported action")
        value = action.get("value", "")
        if not isinstance(value, str) or len(value) > _MAX_ACTION_VALUE:
            raise browser.BrowserError(f"action {index} value must be a string of at most {_MAX_ACTION_VALUE} characters")
        if kind in ("type", "select") and "value" not in action:
            raise browser.BrowserError(f"action {index} requires a value")
        if kind in ("click", "submit") and "value" in action:
            raise browser.BrowserError(f"action {index} does not accept a value")
        result.append({"id": element_id, "action": kind, "value": value})
    return result


def _perform_actions(driver: Any, actions: list[dict[str, str]], proxy_server: Any = None,
                     *, allow_form_post: bool = False) -> list[dict[str, str]]:
    """Apply supported actions; POST submissions require explicit caller consent."""
    outcomes = []
    for action in actions:
        try:
            if action["action"] in ("click", "submit"):
                # Resolve a POST form destination before executing the action. Grant
                # only that exact same-origin URL; GET navigation needs no permit.
                permit_target = driver.execute_script(_ELEMENT_IDS_JS + """
                  const [wanted, kind] = arguments;
                  const nodes = Array.from(document.querySelectorAll(selector));
                  const node = wanted.startsWith('id:')
                    ? document.getElementById(wanted.slice(3))
                    : nodes.find(candidate => idFor(candidate) === wanted) || null;
                  if (!node || !nodes.includes(node) || node.disabled || !node.getClientRects().length ||
                      getComputedStyle(node).visibility === 'hidden' || getComputedStyle(node).display === 'none') return null;
                  const tag = node.tagName.toLowerCase();
                  const type = (node.type || '').toLowerCase();
                  const submits = kind === 'submit'
                    ? (tag === 'form' || (tag === 'button' && (!type || type === 'submit')) ||
                       (tag === 'input' && ['submit','image'].includes(type))
                      )
                    : (kind === 'click' && ((tag === 'button' && (!type || type === 'submit')) ||
                       (tag === 'input' && ['submit','image'].includes(type))));
                  if (!submits) return null;
                  const form = tag === 'form' ? node : node.form;
                  if (!form) return null;
                  // DOM formMethod/formAction properties expose defaults even when
                  // the submitter has no override attribute. Only honor overrides
                  // explicitly present; otherwise inherit from the form itself.
                  const method = tag === 'form' ? form.method :
                    (node.hasAttribute('formmethod') ? node.formMethod : form.method);
                  if ((method || 'get').toLowerCase() !== 'post') return null;
                  const encoding = tag !== 'form' && node.hasAttribute('formenctype')
                    ? node.formEnctype : form.enctype;
                  if ((encoding || '').toLowerCase() !== 'application/x-www-form-urlencoded') return null;
                  const submitter = tag === 'form' ? undefined : node;
                  const data = submitter ? new FormData(form, submitter) : new FormData(form);
                  // URL-encoded HTML form submission normalizes all CR/LF
                  // sequences in names and values to CRLF before percent-encoding.
                  // FormData preserves textarea LF internally, unlike native POST.
                  const entries = Array.from(data.entries()).map(([name, value]) => {
                    if (typeof value !== 'string') return null; // reject file controls
                    const crlf = text => text.replace(/\\r\\n|\\r|\\n/g, '\\r\\n');
                    return [crlf(name), crlf(value)];
                  });
                  if (entries.some(entry => entry === null)) return null;
                  const body = new URLSearchParams(entries).toString();
                  return {target: tag === 'form' ? form.action :
                    (node.hasAttribute('formaction') ? node.formAction : form.action), body};
                """, action["id"], action["action"])
                if permit_target and proxy_server is not None:
                    if not allow_form_post:
                        raise browser.BrowserError(
                            "form POST requires allow_form_post=True caller authorization")
                    if not isinstance(permit_target, dict) or not isinstance(permit_target.get("body"), str):
                        raise browser.BrowserError("form POST could not be represented as URL-encoded data")
                    proxy_server.grant_post_permit(
                        permit_target["target"], permit_target["body"].encode("utf-8"))
            result = driver.execute_script(_ELEMENT_IDS_JS + """
              const [wanted, kind, value] = arguments;
              const nodes = Array.from(document.querySelectorAll(selector));
              let node = null;
              if (wanted.startsWith('id:')) {
                const stableName = wanted.slice(3);
                if (!/^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/.test(stableName)) return {ok:false,error:'invalid stable id'};
                node = document.getElementById(stableName);
              }
              else node = nodes.find(candidate => idFor(candidate) === wanted) || null;
              if (!node || !nodes.includes(node)) return {ok:false, error:'element id is not an interactive element on this page'};
              const tag = node.tagName.toLowerCase();
              if (node.disabled) return {ok:false, error:'disabled elements cannot be acted on'};
              if (!node.getClientRects().length || getComputedStyle(node).visibility === 'hidden' ||
                  getComputedStyle(node).display === 'none')
                return {ok:false, error:'hidden elements cannot be acted on'};
              if (kind === 'click') {
                const clickable = tag === 'a' || tag === 'button' ||
                  (tag === 'input' && ['button','submit','reset','checkbox','radio'].includes((node.type||'').toLowerCase())) ||
                  node.getAttribute('role') === 'button' || node.getAttribute('role') === 'link';
                if (!clickable) return {ok:false, error:'click requires a clickable element'};
                node.click();
              } else if (kind === 'type') {
                if (node.readOnly) return {ok:false, error:'readonly inputs cannot be changed'};
                const type = (node.getAttribute('type') || 'text').toLowerCase();
                if (!(tag === 'textarea' || (tag === 'input' && ['text','search','email','number','tel','url'].includes(type))))
                  return {ok:false, error:'type requires a supported text input'};
                node.focus(); node.value = value;
                node.dispatchEvent(new Event('input', {bubbles:true}));
                node.dispatchEvent(new Event('change', {bubbles:true}));
              } else if (kind === 'select') {
                if (tag !== 'select') return {ok:false, error:'select requires a select element'};
                if (!Array.from(node.options).some(option => option.value === value))
                  return {ok:false, error:'option value not found'};
                node.value = value;
                node.dispatchEvent(new Event('input', {bubbles:true}));
                node.dispatchEvent(new Event('change', {bubbles:true}));
              } else if (kind === 'submit') {
                if (tag === 'form') {
                  if (typeof node.requestSubmit !== 'function') return {ok:false, error:'form submission unavailable'};
                  node.requestSubmit();
                } else if ((tag === 'button' && (node.type === 'submit' || node.type === '')) ||
                           (tag === 'input' && ['submit','image'].includes(node.type))) node.click();
                else return {ok:false, error:'submit requires a form or submit control'};
              }
              return {ok:true, tag};
            """, action["id"], action["action"], action.get("value", ""))
        except browser.BrowserError:
            raise
        except Exception as error:
            raise browser.BrowserError(f"browser action failed: {type(error).__name__}: {error}") from error
        if not isinstance(result, dict) or not result.get("ok"):
            message = result.get("error", "invalid result") if isinstance(result, dict) else "invalid result"
            raise browser.BrowserError(f"action {action['action']} on {action['id']} refused: {message}")
        outcomes.append({"id": action["id"], "action": action["action"], "tag": result["tag"]})
    return outcomes


def _extract_driver(driver: Any, url: str) -> dict[str, Any]:
    """Read rendered DOM using JS after page scripts have run; cap returned data."""
    result = driver.execute_script(_ELEMENT_IDS_JS + """
      const cap = arguments[0];
      const text = (document.body && document.body.innerText) || '';
      const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'))
        .slice(0, 200).map(e => ({level:e.tagName.toLowerCase(), text:(e.innerText||'').slice(0,4096)}));
      const links = Array.from(document.querySelectorAll('a[href]')).slice(0, 200)
        .map(e => ({text:(e.innerText||'').slice(0,4096), url:e.href}));
      const nodes = Array.from(document.querySelectorAll(selector)).slice(0, 256);
      const elements = nodes.map((e, i) => {
        const tag=e.tagName.toLowerCase(), type=(e.getAttribute('type')||'').toLowerCase();
        const label=(e.innerText||e.getAttribute('aria-label')||e.getAttribute('title')||e.getAttribute('name')||'').trim().slice(0,512);
        const item={id:idFor(e),tag,label,disabled:!!e.disabled};
        if (tag==='input') item.type=type||'text';
        if (tag==='select') item.options=Array.from(e.options).slice(0,100).map(o=>({value:o.value,label:(o.label||o.textContent||'').trim().slice(0,256),selected:o.selected}));
        if (tag==='input' || tag==='textarea') item.value=(e.value||'').slice(0,512);
        if (tag==='a') item.href=e.href;
        if (tag==='form') { item.method=(e.method||'get').toLowerCase(); item.action=e.action; }
        return item;
      });
      const stable = Array.from(document.querySelectorAll('[id]')).filter(e =>
        /^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/.test(e.id)).slice(0,64)
        .map(e => ({id:'id:'+e.id,tag:e.tagName.toLowerCase(),label:(e.innerText||e.getAttribute('aria-label')||e.getAttribute('name')||'').trim().slice(0,512)}));
      return {title:document.title||'', text:text.slice(0,cap), text_truncated:text.length>cap,
        headings, headings_truncated:document.querySelectorAll('h1,h2,h3,h4,h5,h6').length>200,
        links, links_truncated:document.querySelectorAll('a[href]').length>200,
        elements, elements_truncated:document.querySelectorAll(selector).length>256, stable_ids:stable,
        ready_state:document.readyState};
    """, MAX_RENDER_TEXT)
    final_url = browser.validate_url(driver.current_url)
    result.update({"url": final_url, "requested_url": url})
    return result


def _configure_firefox_options(options: Any, proxy_port: int, *, headless: bool = True) -> None:
    """Apply the renderer's explicit proxy and no-external-handler policy."""
    if headless:
        options.add_argument("-headless")
    for name, value in (
        ("network.proxy.type", 1),
        ("network.proxy.http", "127.0.0.1"),
        ("network.proxy.http_port", proxy_port),
        ("network.proxy.ssl", "127.0.0.1"),
        ("network.proxy.ssl_port", proxy_port),
        ("network.proxy.no_proxies_on", ""),
        ("network.proxy.allow_hijacking_localhost", True),
        ("network.trr.mode", 5),  # no DNS-over-HTTPS escape
        # Pages are untrusted: never invoke mail clients, handlers, or other apps.
        ("network.protocol-handler.external-default", False),
        ("network.protocol-handler.warn-external-default", False),
    ):
        options.set_preference(name, value)


def _close_runtime(driver: Any, proxy_server: Any, proxy_thread: threading.Thread) -> None:
    """Attempt every cleanup action even if Firefox shutdown itself fails."""
    try:
        if driver is not None:
            driver.quit()
    finally:
        try:
            proxy_server.shutdown()
        finally:
            try:
                proxy_server.server_close()
            finally:
                proxy_thread.join(timeout=2)


def _terminate_process_group(process: Any, *, grace_seconds: float = 2.0) -> None:
    """Best-effort cleanup of a POSIX child process group owned by this run.

    Selenium must start geckodriver in a new session for this to cover Firefox
    descendants. A missing/already-exited process is harmless. This is not an
    OS sandbox or a defense against a process escaping its group.
    """
    if process is None or os.name != "posix":
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        return
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        except OSError:
            break
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.wait(timeout=grace_seconds)
    except Exception:
        pass


def _close_driver_and_service(driver: Any, service: Any) -> None:
    """Bound WebDriver quit, then reap all processes in its owned group."""
    process = getattr(service, "process", None)
    errors: list[BaseException] = []

    def quit_driver() -> None:
        try:
            if driver is not None:
                driver.quit()
        except BaseException as error:
            errors.append(error)

    shutdown = threading.Thread(target=quit_driver, daemon=True)
    shutdown.start()
    shutdown.join(timeout=MAX_DRIVER_QUIT_SECONDS)
    still_running = shutdown.is_alive()
    _terminate_process_group(process)
    if still_running:
        raise browser.BrowserError(
            f"Firefox/WebDriver shutdown exceeded {MAX_DRIVER_QUIT_SECONDS} seconds; process group terminated")
    if errors:
        raise errors[0]


def _bounded_screenshot(driver: Any) -> str:
    """Return a validated PNG payload only when its decoded size is within cap."""
    import base64
    try:
        image = base64.b64decode(driver.get_screenshot_as_base64(), validate=True)
    except Exception as error:
        raise browser.BrowserError(f"invalid screenshot payload: {error}") from error
    if len(image) > MAX_SCREENSHOT_BYTES:
        raise browser.BrowserError(f"screenshot exceeds {MAX_SCREENSHOT_BYTES} byte limit")
    return base64.b64encode(image).decode("ascii")


def _check_wait(wait_ms: Any) -> None:
    if not isinstance(wait_ms, int) or isinstance(wait_ms, bool) or not 0 <= wait_ms <= MAX_WAIT_MS:
        raise browser.BrowserError(f"wait_ms must be between 0 and {MAX_WAIT_MS}")


class _wrap_errors:
    """Report unexpected Selenium/driver failures as BrowserError with their cause."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind: Any, error: Any, _tb: Any) -> bool:
        if error is None or isinstance(error, browser.BrowserError) or not isinstance(error, Exception):
            return False
        raise browser.BrowserError(f"Firefox rendering failed: {type(error).__name__}: {error}") from error


class FirefoxSession:
    """One live Firefox behind its own validating proxy. Element ids stay valid between calls on the same page.

    Started on the first open(); close() reaps Firefox, geckodriver and the proxy. Selenium is imported lazily so
    the stdlib text browser keeps working without it.
    """

    def __init__(self, *, headless: bool = True):
        self.headless = headless
        self.driver: Any = None
        self.service: Any = None
        self.proxy_server: Any = None
        self.proxy_thread: threading.Thread | None = None
        self.requested_url: str | None = None

    def _start(self) -> None:
        if self.proxy_server is not None:
            return
        try:
            from selenium import webdriver
            from selenium.webdriver.firefox.options import Options
            from selenium.webdriver.firefox.service import Service
        except ImportError as error:
            raise browser.BrowserError("rendered browsing requires Selenium and geckodriver (installed by the browser "
                                       "component's module.nix)") from error
        self.proxy_server = _BoundedThreadingHTTPServer(("127.0.0.1", 0), _LoopbackProxy)
        self.proxy_thread = threading.Thread(target=self.proxy_server.serve_forever, daemon=True)
        self.proxy_thread.start()
        options = Options()
        _configure_firefox_options(options, self.proxy_server.server_address[1], headless=self.headless)
        try:
            # Avoid Selenium Manager attempting downloads on a network-restricted VM.
            geckodriver = shutil.which("geckodriver")
            if not geckodriver:
                raise browser.BrowserError("geckodriver not found; add nixpkgs#geckodriver to PATH")
            # Own a fresh POSIX session so geckodriver and Firefox children can be
            # reaped together even if WebDriver's graceful shutdown raises/hangs.
            popen_kw = {"start_new_session": True} if os.name == "posix" else {}
            self.service = Service(executable_path=geckodriver, popen_kw=popen_kw)
            self.driver = webdriver.Firefox(service=self.service, options=options)
            self.driver.set_page_load_timeout(browser.TIMEOUT + 5)
            self.driver.set_script_timeout(MAX_WAIT_MS / 1000 + 5)
        except BaseException:
            self.close()
            raise

    def _navigate(self, url: str, wait_ms: int) -> None:
        canonical = browser.validate_url(url)
        _check_wait(wait_ms)
        self._start()
        # Form POSTs are allowed only to the origin of the page the caller opened.
        initial = urllib.parse.urlsplit(canonical)
        self.proxy_server.allowed_post_origin = (initial.scheme, initial.hostname, initial.port or 80)
        self.requested_url = canonical
        self.driver.get(canonical)
        if wait_ms:
            # A synchronous pause is less fragile than async-script callback lifetime.
            time.sleep(wait_ms / 1000)

    def open(self, url: str, *, wait_ms: int = 500) -> dict[str, Any]:
        """Load a literal-loopback http URL, let its scripts run for wait_ms, and return the page view."""
        with _wrap_errors():
            self._navigate(url, wait_ms)
        return self.view()

    def view(self, *, screenshot: bool = False) -> dict[str, Any]:
        """The current page: title, text, headings, links, interactive elements with ids, stable ids."""
        if self.driver is None or self.requested_url is None:
            raise browser.BrowserError("no page is open; open a URL first")
        with _wrap_errors():
            result = {"schema_version": browser.SCHEMA_VERSION, "engine": "Firefox",
                      "status": 200, "page": _extract_driver(self.driver, self.requested_url)}
            if screenshot:
                result["screenshot_png_base64"] = _bounded_screenshot(self.driver)
        return result

    def act(self, actions: Any, *, allow_form_post: bool = False, wait_ms: int = 500) -> dict[str, Any]:
        """Apply actions ({id, action: click|type|select|submit, value?}) in order, then return the new view."""
        action_list = _validate_actions(actions)
        if not isinstance(allow_form_post, bool):
            raise browser.BrowserError("allow_form_post must be a boolean")
        _check_wait(wait_ms)
        if self.driver is None:
            raise browser.BrowserError("no page is open; open a URL first")
        with _wrap_errors():
            outcomes = _perform_actions(self.driver, action_list, self.proxy_server, allow_form_post=allow_form_post)
        if outcomes:
            # Actions can trigger a real navigation (including a 303 POST->GET).
            # The caller's JS settle delay may be zero or tiny; retain a bounded
            # navigation grace period so extraction does not race document unload.
            time.sleep(min(max(wait_ms / 1000, 0.5), 1.0))
        result = self.view()
        if outcomes:
            result["actions"] = outcomes
        return result

    def close(self) -> None:
        """Quit Firefox (bounded), reap its process group and stop the proxy. Safe to call twice."""
        if self.proxy_server is None:
            return
        driver, service, proxy_server, proxy_thread = self.driver, self.service, self.proxy_server, self.proxy_thread
        self.driver = self.service = self.proxy_server = self.proxy_thread = self.requested_url = None
        try:
            _close_driver_and_service(driver, service)
        finally:
            _close_runtime(None, proxy_server, proxy_thread)

    def __enter__(self) -> "FirefoxSession":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def rendered_browse(url: str, *, wait_ms: int = 500, screenshot: bool = False,
                    headless: bool = True, actions: Any = None,
                    allow_form_post: bool = False) -> dict[str, Any]:
    """One shot: render a loopback page in a fresh Firefox, apply actions, return bounded DOM data, close.

    Browser traffic (including redirects/assets) is constrained by the local proxy.
    """
    canonical = browser.validate_url(url)
    action_list = _validate_actions([] if actions is None else actions)
    if not isinstance(allow_form_post, bool):
        raise browser.BrowserError("allow_form_post must be a boolean")
    _check_wait(wait_ms)
    with FirefoxSession(headless=headless) as session:
        with _wrap_errors():
            session._navigate(canonical, wait_ms)
        if action_list:
            result = session.act(action_list, allow_form_post=allow_form_post, wait_ms=wait_ms)
        else:
            result = session.view()
        if screenshot:
            with _wrap_errors():
                result["screenshot_png_base64"] = _bounded_screenshot(session.driver)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--wait-ms", type=int, default=500, help="post-load JS settling delay (0-10000)")
    parser.add_argument("--screenshot", action="store_true", help="include base64 PNG screenshot in JSON")
    parser.add_argument("--visible", action="store_true", help="use the active desktop display instead of headless mode")
    parser.add_argument("--actions-json", help="JSON array of bounded actions: {id,action,value?}")
    parser.add_argument("--allow-form-post", action="store_true",
                        help="explicitly authorize a bounded same-origin URL-encoded form POST")
    args = parser.parse_args(argv)
    try:
        actions = json.loads(args.actions_json) if args.actions_json is not None else None
        result = rendered_browse(args.url, wait_ms=args.wait_ms, screenshot=args.screenshot,
                                 headless=not args.visible, actions=actions,
                                 allow_form_post=args.allow_form_post)
    except browser.BrowserError as error:
        print(f"odd1-browser render: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
