#!/usr/bin/env python3
"""Bounded live probe of broker worker-cap rejection (not socket-backlog EAGAIN).

Starts a temporary, non-aggregate broker, pins each bounded request worker with
an incomplete request, then requires the next accepted connection to receive the
explicit BusyError reply. Releases the clients and confirms the broker responds
again. This is a smoke test of admission control, not sustained overload or a
performance/fairness benchmark.
"""
import errno
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

PROJECT = pathlib.Path(__file__).resolve().parents[2]
BROKER = PROJECT / 'any_app_broker' / 'broker.py'
MAX_WORKERS = 16
MAX_LINE = 4096
CONNECT_RETRY_SECONDS = 3


def connect_with_retry(sock, address, timeout=CONNECT_RETRY_SECONDS):
    """Boundedly retry transient Unix-listener backlog EAGAIN, not worker semantics."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            sock.connect(str(address))
            return
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK) or time.monotonic() >= deadline:
                raise
            time.sleep(.02)


def read_line(sock, limit=MAX_LINE):
    data = bytearray()
    while b'\n' not in data:
        chunk = sock.recv(min(1024, limit + 1 - len(data)))
        if not chunk:
            raise RuntimeError('broker closed before sending a complete line')
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError('broker response exceeded byte limit')
    end = data.index(b'\n') + 1
    if end > limit:
        raise ValueError('broker response exceeded byte limit')
    return json.loads(bytes(data[:end]))


def main():
    if os.environ.get('DISPLAY') is None:
        raise RuntimeError('run only from an active Orbit Desktop GUI session')
    with tempfile.TemporaryDirectory(prefix='orbit-capacity-probe-') as td:
        root = pathlib.Path(td)
        sockpath = root / 'broker.sock'
        command = [sys.executable, str(BROKER), '--socket', str(sockpath),
                   '--root', str(root / 'sessions'), '--evidence-root', str(root / 'evidence'), 'serve']
        broker = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, start_new_session=True)
        held = []
        try:
            for _ in range(100):
                if broker.poll() is not None:
                    raise RuntimeError('broker exited: ' + broker.stderr.read().decode(errors='replace')[-2000:])
                if sockpath.exists():
                    break
                time.sleep(.05)
            else:
                raise TimeoutError('broker socket did not become ready')

            # Each partial request occupies a Handler until its bounded 5s read
            # timeout. Leave ample time to separate acceptance from listen backlog.
            for _ in range(MAX_WORKERS):
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.settimeout(2)
                connect_with_retry(client, sockpath)
                client.sendall(b'{')
                held.append(client)

            # Wait for every worker to enter its read wait, then ensure one
            # further connection is handled immediately by the worker cap.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if len(os.listdir(f'/proc/{broker.pid}/task')) >= MAX_WORKERS + 1:
                    break
                time.sleep(.02)
            else:
                raise TimeoutError('could not observe all request workers before saturation probe')

            overflow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            overflow.settimeout(2)
            try:
                overflow.connect(str(sockpath))
                reply = read_line(overflow)
            finally:
                overflow.close()
            if reply.get('ok') is not False or reply.get('error_type') != 'BusyError':
                raise RuntimeError(f'expected explicit BusyError, got {reply!r}')

            for client in held:
                client.close()
            held.clear()
            # The ordinary handler read deadline bounds release even if close is
            # not observed immediately. Poll using completed framed requests.
            recovery = None
            deadline = time.monotonic() + 7
            while time.monotonic() < deadline:
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as check:
                        check.settimeout(.5)
                        connect_with_retry(check, sockpath, timeout=.5)
                        check.sendall(b'{"op":"__capacity_recovery_probe__"}\n')
                        recovery = read_line(check)
                    if recovery.get('error_type') == 'ValueError':
                        break
                except (OSError, TimeoutError, ValueError, RuntimeError):
                    time.sleep(.1)
            if not isinstance(recovery, dict) or recovery.get('error_type') != 'ValueError':
                raise RuntimeError(f'broker did not recover after worker release: {recovery!r}')
            print(json.dumps({'ok': True, 'workers_held': MAX_WORKERS,
                              'overflow_error_type': reply['error_type'],
                              'post_release_error_type': recovery['error_type'],
                              'scope': 'one temporary broker; incomplete requests; no GUI sessions; no aggregate cgroup'},
                             sort_keys=True))
        finally:
            for client in held:
                client.close()
            if broker.poll() is None:
                broker.terminate()
                try:
                    broker.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    broker.kill()
                    broker.wait(timeout=2)
                    raise RuntimeError('temporary broker required SIGKILL during cleanup')
            if broker.returncode != 0:
                raise RuntimeError(f'temporary broker exit {broker.returncode}: '
                                   + broker.stderr.read().decode(errors='replace')[-2000:])
            if sockpath.exists():
                raise RuntimeError('temporary broker socket remains after shutdown')
            sessions = root / 'sessions'
            if sessions.exists() and any(sessions.iterdir()):
                raise RuntimeError('temporary broker left session scratch')


if __name__ == '__main__':
    main()
