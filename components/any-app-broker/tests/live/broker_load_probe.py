#!/usr/bin/env python3
"""Bounded Orbit Desktop-only concurrent observation probe for the broker.

Uses two disposable sessions and twelve bounded observe requests, with four
requests in flight to stay within the local Unix-listener backlog.
It records timings and basic success counts; it is a smoke/latency signal, not a
statistical performance benchmark or a safety/isolation proof.
"""
import concurrent.futures
import json
import os
import pathlib
import socket
import statistics
import subprocess
import sys
import tempfile
import time

PROJECT = pathlib.Path(__file__).resolve().parents[2]
BROKER = PROJECT / 'any_app_broker' / 'broker.py'
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def read_response_line(client, max_bytes=MAX_RESPONSE_BYTES):
    """Read one newline-terminated JSON response, bounded even for a bad broker."""
    data = bytearray()
    while b'\n' not in data:
        remaining = max_bytes + 1 - len(data)
        if remaining <= 0:
            raise ValueError('broker response exceeded byte limit')
        chunk = client.recv(min(65536, remaining))
        if not chunk:
            raise RuntimeError('broker closed response early')
        data.extend(chunk)
        if len(data) > max_bytes:
            raise ValueError('broker response exceeded byte limit')
    end = data.index(b'\n') + 1
    if end > max_bytes:
        raise ValueError('broker response exceeded byte limit')
    return bytes(data[:end])


def request(sock, payload, timeout=15):
    started = time.monotonic()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(sock))
        client.sendall(json.dumps(payload, separators=(',', ':')).encode() + b'\n')
        data = read_response_line(client)
    response = json.loads(data)
    elapsed = time.monotonic() - started
    if not response.get('ok'):
        raise RuntimeError(response)
    return response['result'], elapsed


def percentile(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int((len(ordered) * p + 0.999999)) - 1))]


def main():
    if os.environ.get('DISPLAY') is None:
        raise RuntimeError('run only from the active Orbit Desktop GUI session')
    with tempfile.TemporaryDirectory(prefix='orbit-load-probe-') as td:
        root = pathlib.Path(td)
        sock = root / 'broker.sock'
        sessions = []
        env = os.environ.copy()
        env['PATH'] = (env.get('PATH', '') + ':/nix/store/9k5xd4nbyz9icrmnj6y92lw4szf4j5ng-xauth-1.1.4/bin'
                       ':/nix/store/f9nyq02plppm68j3w5if3sb7ndk7s45w-xorg-server-21.1.20/bin')
        command = [sys.executable, str(BROKER), '--socket', str(sock),
                   '--root', str(root / 'sessions'), '--evidence-root', str(root / 'evidence'),
                   '--aggregate-limits', 'serve']
        broker = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, env=env, start_new_session=True)
        try:
            ready = False
            for _ in range(100):
                if broker.poll() is not None:
                    raise RuntimeError('broker exited: ' + broker.stderr.read().decode()[-2000:])
                if sock.exists():
                    try:
                        # Any well-formed broker response proves the listener is
                        # accepting requests; the deliberately unsupported op is
                        # expected to return ok=false.
                        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                            probe.settimeout(1)
                            probe.connect(str(sock))
                            probe.sendall(b'{"op":"__readiness_probe__"}\n')
                            response = read_response_line(probe, max_bytes=4096)
                        parsed = json.loads(response)
                        ready = isinstance(parsed, dict) and 'ok' in parsed
                        if ready:
                            break
                    except (OSError, ValueError, socket.timeout):
                        pass
                time.sleep(.1)
            if not ready:
                raise TimeoutError('broker socket did not become ready')
            for _ in range(2):
                result, _ = request(sock, {'op': 'create'})
                sessions.append({'session_id': result['session_id'],
                                 'capability': result['capability']})
            tasks = [(session['session_id'], {'op': 'observe',
                                               'session_id': session['session_id'],
                                               'capability': session['capability']})
                     for session in sessions for _ in range(6)]
            timings = []
            def observe(item):
                sid, payload = item
                result, elapsed = request(sock, payload)
                if not result.get('frame_id') or not result.get('frame_base64'):
                    raise RuntimeError(f'observation lacks frame data for {sid}')
                return elapsed, len(result['frame_base64'])
            # Four requests in flight sample moderate pressure without exceeding
            # the local Unix-socket backlog. This does not test overload rejection.
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                observations = list(pool.map(observe, tasks))
            timings = [item[0] for item in observations]
            report = {'ok': True, 'sessions': len(sessions), 'requests_total': len(timings),
                      'max_in_flight': 4, 'successes': len(timings), 'failures': 0,
                      'latency_seconds': {'p50': round(statistics.median(timings), 4),
                                          'p95': round(percentile(timings, .95), 4),
                                          'max': round(max(timings), 4)},
                      'frame_base64_bytes': {'min': min(item[1] for item in observations),
                                             'max': max(item[1] for item in observations)},
                      'scope': 'two isolated disposable GUI sessions; aggregate limits enabled'}
            print(json.dumps(report, sort_keys=True))
        finally:
            if broker.poll() is None:
                try:
                    broker.terminate()
                    broker.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    broker.kill()
                    broker.wait(timeout=3)
                    raise RuntimeError('broker failed to shut down gracefully; inspect its owned session cgroups')
            if broker.returncode != 0:
                raise RuntimeError(f'broker exit {broker.returncode}: {broker.stderr.read().decode()[-2000:]}')
            for path in (root / 'sessions',):
                if path.exists() and any(path.iterdir()):
                    raise RuntimeError(f'broker left session residue: {list(path.iterdir())}')


if __name__ == '__main__':
    main()
