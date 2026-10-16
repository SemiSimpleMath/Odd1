"""The courier: the only code that resolves a secret pod's value, and only to put it where the pod allows.

An agent hands the courier references, never values:
- http(method, url, headers, body): `{{pod:secret.token:...}}` placeholders in headers or body become the value,
  when the pod allows `host:<the url's host>`.
- run(command, env): environment variables set from pods, when the pod allows `env:<VAR>`.
Whatever comes back (response, output, errors) has every resolved value scrubbed out, plain and in its common
encodings (base64, URL, hex), replaced by the pod's reference. A program that transforms a secret in other ways could
still reveal it: give `env:` only to programs you trust with it.
"""
from __future__ import annotations

import base64
import binascii
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from .store import PodError, Pods, actor

PLACEHOLDER = re.compile(r"\{\{(pod:[a-z][a-z0-9.\-]{0,40}:[A-Za-z0-9_\-]{6,64})\}\}")
MAX_OUT = 20_000


def scrub(text: str, secrets: dict[str, str]) -> str:
    """Replace each value (and its base64, URL and hex forms) by [its reference]."""
    for uri, value in secrets.items():
        forms = {value, urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value),
                 base64.b64encode(value.encode()).decode().rstrip("="), binascii.hexlify(value.encode()).decode()}
        for form in sorted((f for f in forms if len(f) >= 4), key=len, reverse=True):
            text = text.replace(form, f"[{uri}]")
    return text


def _resolve(pods: Pods, text: str, destination: str, who: str, found: dict) -> str:
    def one(m):
        uri = m.group(1)
        found[uri] = pods._courier_value(uri, destination, who)
        return found[uri]
    return PLACEHOLDER.sub(one, text)


def http(pods: Pods, method: str, url: str, headers: dict | None = None, body: str = "",
         timeout: float = 60) -> dict:
    """Send a request with pod placeholders resolved for this host; the reply comes back scrubbed."""
    who, found = actor(), {}
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" and parts.hostname not in ("localhost", "127.0.0.1"):
        raise PodError("secrets travel over https only")
    if PLACEHOLDER.search(url):
        raise PodError("put pod placeholders in headers or the body, not the URL (URLs end up in logs)")
    destination = f"host:{parts.hostname}"
    sent = {k: _resolve(pods, v, destination, who, found) for k, v in (headers or {}).items()}
    data = _resolve(pods, body, destination, who, found).encode() if body else None
    request = urllib.request.Request(url, data=data, headers=sent, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as r:
            status, reply_headers, text = r.status, dict(r.headers), r.read(MAX_OUT).decode(errors="replace")
    except urllib.error.HTTPError as e:
        status, reply_headers, text = e.code, dict(e.headers or {}), e.read(MAX_OUT).decode(errors="replace")
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "error": scrub(str(e), found), "pods": sorted(found)}
    return {"ok": 200 <= status < 300, "status": status, "pods": sorted(found),
            "headers": {k: scrub(v, found) for k, v in reply_headers.items()}, "body": scrub(text, found)}


def run(pods: Pods, command: list[str], env: dict[str, str], cwd: str | None = None, timeout: float = 600) -> dict:
    """Run a command with environment variables set from pods (each pod must allow env:<VAR>); output scrubbed."""
    who, found = actor(), {}
    extra = {}
    for var, uri in env.items():
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", var):
            raise PodError(f"not an environment variable name: {var!r}")
        extra[var] = pods._courier_value(uri, f"env:{var}", who)
        found[uri] = extra[var]
    try:
        p = subprocess.run(command, cwd=cwd, env=dict(os.environ, **extra), capture_output=True, text=True,
                           timeout=timeout)
        code, out, err = p.returncode, p.stdout[-MAX_OUT:], p.stderr[-MAX_OUT:]
    except subprocess.TimeoutExpired as e:
        code, out, err = 124, (e.stdout or "")[-MAX_OUT:] if isinstance(e.stdout, str) else "", "timed out"
    except OSError as e:
        code, out, err = 127, "", str(e)
    return {"exit": code, "stdout": scrub(out, found), "stderr": scrub(err, found), "pods": sorted(found)}
