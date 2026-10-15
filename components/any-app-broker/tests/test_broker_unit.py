#!/usr/bin/env python3
"""Unit regression tests for broker failure-path cleanup; needs no X server, but needs Linux (fcntl, Unix sockets)."""
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import pytest

if not sys.platform.startswith('linux'):
    pytest.skip('broker uses Linux-only fcntl, Unix sockets and process groups', allow_module_level=True)

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'any_app_broker'))
import broker  # noqa: E402


class CreateFailureCleanupTests(unittest.TestCase):
    def test_session_capacity_rejects_extra_session_without_leaking_resources(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-unit-') as td:
            root = pathlib.Path(td) / 'sessions'
            instance = broker.Broker(root)
            fake_sessions = [mock.Mock(last=broker.now(), close=mock.Mock())
                             for _ in range(broker.MAX_SESSIONS)]
            with mock.patch.object(broker, 'require_binary', return_value='xauth'), \
                 mock.patch.object(broker.subprocess, 'run'), \
                 mock.patch.object(broker, 'Session', side_effect=fake_sessions):
                for _ in range(broker.MAX_SESSIONS):
                    instance.create()
                self.assertEqual(len(instance.sessions), broker.MAX_SESSIONS)
                before_dirs = set(root.iterdir())
                before_displays = set(instance.displays)
                with self.assertRaisesRegex(RuntimeError, 'session capacity reached'):
                    instance.create()
                self.assertEqual(set(root.iterdir()), before_dirs)
                self.assertEqual(instance.displays, before_displays)
                self.assertEqual(len(instance.sessions), broker.MAX_SESSIONS)
            for fake in fake_sessions:
                fake.close()
            for child in list(root.iterdir()):
                shutil.rmtree(child)
            instance.sessions.clear()
            instance.tokens.clear()
            instance.displays.clear()

    def test_missing_xauth_cleans_session_directory_and_display_reservation(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-unit-') as td:
            root = pathlib.Path(td) / 'sessions'
            instance = broker.Broker(root)
            with mock.patch.object(broker, 'require_binary', side_effect=RuntimeError('missing xauth')):
                with self.assertRaisesRegex(RuntimeError, 'missing xauth'):
                    instance.create()
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(instance.displays, set())
            self.assertEqual(instance.sessions, {})
            self.assertEqual(instance.tokens, {})

    def test_xauth_command_failure_cleans_session_directory_and_display_reservation(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-unit-') as td:
            root = pathlib.Path(td) / 'sessions'
            instance = broker.Broker(root)
            failure = broker.subprocess.CalledProcessError(1, ['xauth'])
            with mock.patch.object(broker, 'require_binary', return_value='xauth'), \
                 mock.patch.object(broker.subprocess, 'run', side_effect=failure):
                with self.assertRaises(broker.subprocess.CalledProcessError):
                    instance.create()
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(instance.displays, set())
            self.assertEqual(instance.sessions, {})
            self.assertEqual(instance.tokens, {})

    def test_x_server_startup_failure_cleans_session_directory_and_display_reservation(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-unit-') as td:
            root = pathlib.Path(td) / 'sessions'
            instance = broker.Broker(root)
            with mock.patch.object(broker, 'require_binary', return_value='xauth'), \
                 mock.patch.object(broker.subprocess, 'run'), \
                 mock.patch.object(broker, 'Session', side_effect=RuntimeError('X startup failed')):
                with self.assertRaisesRegex(RuntimeError, 'X startup failed'):
                    instance.create()
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(instance.displays, set())
            self.assertEqual(instance.sessions, {})
            self.assertEqual(instance.tokens, {})


class RecoveredSessionDirectoryTests(unittest.TestCase):
    def test_prunes_only_owner_owned_session_dirs(self):
        with tempfile.TemporaryDirectory(prefix='orbit-session-recovery-') as td:
            root = pathlib.Path(td) / 'sessions'
            root.mkdir()
            orphan = root / ('a' * 32 + '-dead-session')
            orphan.mkdir()
            (orphan / 'app-state.json').write_text('{}')
            unrelated = root / 'operator-data'
            unrelated.mkdir()
            stranger = root / ('z' * 32 + '-link')
            stranger.symlink_to(unrelated, target_is_directory=True)
            broker.remove_recovered_session_directories(root)
            self.assertFalse(orphan.exists())
            self.assertTrue(unrelated.is_dir())
            self.assertTrue(stranger.is_symlink())

    def test_nonmatching_paths_are_preserved(self):
        with tempfile.TemporaryDirectory(prefix='orbit-session-no-prune-') as td:
            root = pathlib.Path(td) / 'sessions'
            root.mkdir()
            orphan = root / ('a' * 32 + '-dead-session')
            orphan.mkdir()
            unrelated = root / 'operator-data'
            unrelated.mkdir()
            broker.remove_recovered_session_directories(root)
            self.assertFalse(orphan.exists())
            self.assertTrue(unrelated.is_dir())

    def test_pruning_refuses_owned_session_symlink_and_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix='orbit-session-unsafe-') as td:
            root = pathlib.Path(td) / 'sessions'
            root.mkdir()
            elsewhere = pathlib.Path(td) / 'elsewhere'
            elsewhere.mkdir()
            link = root / ('a' * 32 + '-dead-session')
            link.symlink_to(elsewhere, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'unsafe orphan session path'):
                broker.remove_recovered_session_directories(root)
            self.assertTrue(elsewhere.is_dir())


class BrokerLoadProbeTests(unittest.TestCase):
    def load_probe(self):
        import importlib.util
        path = ROOT / 'tests' / 'live' / 'broker_load_probe.py'
        spec = importlib.util.spec_from_file_location('broker_load_probe', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_bounded_response_reader_accepts_newline_across_chunks(self):
        from unittest import mock
        probe = self.load_probe()
        client = mock.Mock()
        client.recv.side_effect = [b'{"ok":true', b'}\nextra']
        self.assertEqual(probe.read_response_line(client, max_bytes=32), b'{"ok":true}\n')
        self.assertEqual(client.recv.call_count, 2)

    def test_bounded_response_reader_rejects_unterminated_oversized_response(self):
        from unittest import mock
        probe = self.load_probe()
        client = mock.Mock()
        client.recv.side_effect = [b'x' * 8, b'y']
        with self.assertRaisesRegex(ValueError, 'exceeded byte limit'):
            probe.read_response_line(client, max_bytes=8)

    def test_bounded_response_reader_rejects_oversized_line_with_newline(self):
        from unittest import mock
        probe = self.load_probe()
        client = mock.Mock()
        client.recv.return_value = b'x' * 9 + b'\n'
        with self.assertRaisesRegex(ValueError, 'exceeded byte limit'):
            probe.read_response_line(client, max_bytes=8)

    def test_capacity_probe_line_reader_is_bounded(self):
        import importlib.util
        from unittest import mock
        path = ROOT / 'tests' / 'live' / 'broker_capacity_probe.py'
        spec = importlib.util.spec_from_file_location('broker_capacity_probe', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        client = mock.Mock()
        client.recv.side_effect = [b'{"ok":', b'true}\nextra']
        self.assertEqual(module.read_line(client), {'ok': True})
        client.recv.side_effect = [b'x' * 16]
        with self.assertRaisesRegex(ValueError, 'exceeded byte limit'):
            module.read_line(client, limit=8)


class WorkerCapacityTests(unittest.TestCase):
    def test_worker_limit_must_be_positive_integer(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-workers-') as td:
            for invalid in (0, -1, 1.5, True):
                with self.subTest(invalid=invalid):
                    with self.assertRaisesRegex(ValueError, 'positive integer'):
                        broker.UnixServer(str(pathlib.Path(td) / f'{invalid}.sock'),
                                          broker.Handler, max_workers=invalid)

    def test_excess_connections_rejected_and_worker_slot_released(self):
        import json
        import socket
        import threading
        import time

        class EchoBroker:
            def dispatch(self, req, deadline=None):
                return {'echo': req}

        with tempfile.TemporaryDirectory(prefix='orbit-broker-workers-') as td:
            path = str(pathlib.Path(td) / 'broker.sock')
            server = broker.UnixServer(path, broker.Handler, max_workers=1)
            server.broker = EchoBroker()
            server.request_read_timeout = 2
            server.response_write_timeout = 1
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as held:
                    held.settimeout(2)
                    held.connect(path)  # occupies the only request worker
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as excess:
                        excess.settimeout(2)
                        excess.connect(path)
                        busy = json.loads(excess.recv(1024))
                        self.assertFalse(busy['ok'])
                        self.assertEqual(busy['error_type'], 'BusyError')
                    held.sendall(b'{"op":"first"}\n')
                    response = b''
                    while not response.endswith(b'\n'):
                        response += held.recv(1024)
                    self.assertTrue(json.loads(response)['ok'])
                # Reading the reply can race the handler's finally block releasing
                # the worker permit. Wait for the lifecycle event rather than making
                # this regression depend on scheduler timing.
                release_deadline = time.monotonic() + 1
                while not server._worker_slots.acquire(blocking=False):
                    if time.monotonic() >= release_deadline:
                        self.fail('worker permit was not released after handler exit')
                    time.sleep(0.005)
                server._worker_slots.release()
                # The first handler exited, so a new connection can now be served.
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as next_client:
                    next_client.settimeout(2)
                    next_client.connect(path)
                    next_client.sendall(b'{"op":"next"}\n')
                    response = b''
                    while not response.endswith(b'\n'):
                        response += next_client.recv(1024)
                    self.assertTrue(json.loads(response)['ok'])
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=2)


if __name__ == '__main__':
    unittest.main()


class RequestDeadlineTests(unittest.TestCase):
    def test_slow_client_gets_absolute_read_deadline(self):
        import json
        import socket
        import threading
        import time

        class EchoBroker:
            def dispatch(self, req, deadline=None):
                return {'echo': req}

        with tempfile.TemporaryDirectory(prefix='orbit-broker-socket-') as td:
            path = str(pathlib.Path(td) / 'broker.sock')
            server = broker.UnixServer(path, broker.Handler)
            server.broker = EchoBroker()
            server.request_read_timeout = 0.15
            server.response_write_timeout = 1
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(2)
                    client.connect(path)
                    started = time.monotonic()
                    payload = b'{"op":"wait"}'
                    def trickle():
                        try:
                            for byte in payload:
                                client.sendall(bytes([byte]))
                                time.sleep(0.08)  # Each byte is within timeout; total request is not.
                        except (BrokenPipeError, ConnectionResetError):
                            pass  # Server may already have enforced the absolute deadline.
                    sender = threading.Thread(target=trickle, daemon=True)
                    sender.start()
                    response = b''
                    while not response.endswith(b'\n'):
                        response += client.recv(1024)
                    elapsed = time.monotonic() - started
                    sender.join(timeout=2)
                    decoded = json.loads(response)
                    self.assertEqual(decoded['error_type'], 'TimeoutError')
                    self.assertLess(elapsed, 1)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=2)

    def test_oversized_line_rejected_before_dispatch(self):
        import json
        import socket
        import threading

        class EchoBroker:
            def dispatch(self, req, deadline=None):
                raise AssertionError('oversized request must not be dispatched')

        with tempfile.TemporaryDirectory(prefix='orbit-broker-socket-') as td:
            path = str(pathlib.Path(td) / 'broker.sock')
            server = broker.UnixServer(path, broker.Handler)
            server.broker = EchoBroker()
            server.request_read_timeout = 1
            server.response_write_timeout = 1
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(2)
                    client.connect(path)
                    client.sendall(b'x' * (broker.MAX_MESSAGE + 1) + b'\n')
                    response = b''
                    while not response.endswith(b'\n'):
                        response += client.recv(1024)
                    decoded = json.loads(response)
                    self.assertFalse(decoded['ok'])
                    self.assertEqual(decoded['error'], 'invalid request size')
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=2)


class DispatchBudgetTests(unittest.TestCase):
    def test_budget_expires_and_cannot_be_extended(self):
        import time
        deadline = broker.DispatchDeadline(0.03)
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, 'during wait'):
            deadline.sleep(0.2)
        self.assertLess(time.monotonic() - started, 0.5)
        with self.assertRaisesRegex(TimeoutError, 'deadline exceeded'):
            deadline.check()

    def test_subprocess_timeout_is_capped_by_remaining_dispatch_budget(self):
        import time
        deadline = broker.DispatchDeadline(0.1)
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, 'during subprocess'):
            deadline.run([sys.executable, '-c', 'import time; time.sleep(5)'], timeout=5)
        self.assertLess(time.monotonic() - started, 1)

    def test_deadline_rejects_nonfinite_and_boolean_values(self):
        for invalid in (True, 0, -1, float('inf'), float('nan')):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, 'finite positive'):
                    broker.DispatchDeadline(invalid)

    def test_wait_rejects_nonfinite_boolean_and_out_of_range_values(self):
        for invalid in (True, float('inf'), float('nan'), -0.1, 5.1):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, 'wait must be finite'):
                    broker.Session.execute(self._session_with_deadline(),
                                           {'op':'wait','seconds':invalid,'frame_id':'f'})

    def test_click_rejects_old_or_foreign_observation_without_dispatch(self):
        from types import SimpleNamespace
        session = SimpleNamespace(deadline=broker.DispatchDeadline(1),
                                  frames={'current': 3, 'old': 2}, revision=3)
        for frame_id in ('old', 'foreign', None):
            with self.subTest(frame_id=frame_id), \
                 mock.patch.object(broker, 'limited_command') as limited, \
                 self.assertRaisesRegex(ValueError, 'stale or foreign frame_id'):
                broker.Session.execute(session, {'op':'click', 'frame_id':frame_id, 'x':4, 'y':5})
            limited.assert_not_called()

    def test_input_dispatch_invalidates_observation_for_next_action(self):
        from types import SimpleNamespace
        session = SimpleNamespace(deadline=broker.DispatchDeadline(1),
                                  frames={'f': 7}, revision=7, id='s', env={},
                                  broker=SimpleNamespace(owner_unit=None),
                                  touch=lambda: None)
        completed = broker.subprocess.CompletedProcess([], 0, b'', b'')
        with mock.patch.object(broker, 'require_binary', return_value='xdotool'), \
             mock.patch.object(broker, 'limited_command', side_effect=lambda cmd: cmd), \
             mock.patch.object(broker, 'scoped_command', side_effect=lambda sid, cmd, **kwargs: cmd), \
             mock.patch.object(session.deadline, 'run', return_value=completed) as run:
            result = broker.Session.execute(session, {'op':'click', 'frame_id':'f', 'x':4, 'y':5})
        self.assertEqual(result['revision'], 8)
        self.assertFalse(result['verified'])
        self.assertEqual(session.revision, 8)
        run.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'stale or foreign frame_id'):
            broker.Session.execute(session, {'op':'click', 'frame_id':'f', 'x':4, 'y':5})

    @staticmethod
    def _session_with_deadline():
        from types import SimpleNamespace
        return SimpleNamespace(deadline=broker.DispatchDeadline(1), touch=lambda: None,
                               frames={'f': 1}, revision=1)

    def test_broker_dispatch_passes_one_budget_to_session_and_never_reports_late_success(self):
        import threading
        import time
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory(prefix='orbit-broker-budget-') as td:
            instance = broker.Broker(pathlib.Path(td) / 'sessions')

            class SlowSession:
                lock = threading.RLock()
                last = broker.now()
                display = ':97'
                def touch(self): self.last = broker.now()
                def execute(self, req): self.deadline.sleep(req['seconds'])
                def close(self): pass

            session = SlowSession()
            instance.sessions['s'] = session
            instance.tokens['s'] = 'cap'
            instance.displays.add(97)
            started = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, 'during wait'):
                instance.dispatch({'op':'wait','session_id':'s','capability':'cap',
                                   'seconds':0.2}, broker.DispatchDeadline(0.03))
            self.assertLess(time.monotonic() - started, 0.5)

    def test_session_startup_failure_uses_remaining_cleanup_budget_and_reaps_children(self):
        import threading
        from types import SimpleNamespace

        deadline = broker.DispatchDeadline(0.1)
        import time
        fake_proc = mock.Mock(poll=mock.Mock(return_value=None), pid=123456,
                              wait=mock.Mock(side_effect=broker.subprocess.TimeoutExpired('Xvfb', 0.1)))
        fake_session = SimpleNamespace(procs=[fake_proc], path=pathlib.Path('/nonexistent'),
                                       broker=SimpleNamespace(aggregate_limits=False, owner_unit=None),
                                       close=lambda deadline=None: broker.Session.close(fake_session, deadline))
        def slow_popen(*_args, **_kwargs):
            time.sleep(0.12)  # consume the dispatch budget after the child exists
            return fake_proc
        with mock.patch.object(broker, 'require_binary', return_value='/fake/Xvfb'), \
             mock.patch.object(broker.subprocess, 'Popen', side_effect=slow_popen), \
             mock.patch.object(broker.os, 'killpg') as killpg:
            with self.assertRaisesRegex(TimeoutError, 'dispatch deadline exceeded'):
                broker.Session.__init__(fake_session, mock.Mock(aggregate_limits=False, owner_unit=None), 'sid',
                                       pathlib.Path('/nonexistent'), ':99', 'cookie',
                                       '/nonexistent/auth', deadline)
        self.assertIn(mock.call(fake_proc.pid, broker.signal.SIGTERM), killpg.call_args_list)
        self.assertIn(mock.call(fake_proc.pid, broker.signal.SIGKILL), killpg.call_args_list)
        fake_proc.wait.assert_called_once()

    def test_close_releases_session_capacity_even_when_cleanup_exhausts_budget(self):
        import threading
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory(prefix='orbit-broker-close-') as td:
            instance = broker.Broker(pathlib.Path(td) / 'sessions')

            class SlowCloseSession:
                lock = threading.RLock()
                last = broker.now()
                display = ':98'
                def touch(self): self.last = broker.now()
                def execute(self, req):
                    raise TimeoutError('dispatch deadline exceeded during close')
                def close(self, deadline=None): pass

            session = SlowCloseSession()
            instance.sessions['s'] = session
            instance.tokens['s'] = 'cap'
            instance.displays.add(98)
            with self.assertRaisesRegex(TimeoutError, 'during close'):
                instance.dispatch({'op':'close','session_id':'s','capability':'cap'},
                                  broker.DispatchDeadline(0.1))
            self.assertNotIn('s', instance.sessions)
            self.assertNotIn('s', instance.tokens)
            self.assertNotIn(98, instance.displays)

    def test_lock_wait_is_limited_by_dispatch_budget(self):
        import threading
        import time
        lock = threading.Lock()
        lock.acquire()
        try:
            deadline = broker.DispatchDeadline(0.05)
            started = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, 'waiting for broker lock'):
                with deadline.hold(lock):
                    self.fail('contended lock acquired')
            self.assertLess(time.monotonic() - started, 0.5)
        finally:
            lock.release()


class AggregateCgroupHelperTests(unittest.TestCase):
    def test_project_lock_rejects_symlink_directory_or_lock_file(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-lock-path-') as td:
            base = pathlib.Path(td)
            target = base / 'target'
            target.mkdir()
            runtime = base / 'runtime'
            runtime.mkdir()
            link_dir = runtime / 'orbit-any-app'
            link_dir.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'owner-owned directory'):
                broker.acquire_project_lock(link_dir / 'lock')
            link_file = runtime / 'linked-lock'
            link_file.symlink_to(target / 'victim')
            with self.assertRaises(OSError):
                broker.acquire_project_lock(link_file)
            self.assertFalse((target / 'victim').exists())

    def test_aggregate_startup_prunes_session_dirs_only_after_slice_recovery(self):
        events = []
        with tempfile.TemporaryDirectory(prefix='orbit-recover-order-') as td:
            root = pathlib.Path(td) / 'sessions'
            root.mkdir()
            orphan = root / ('a' * 32 + '-dead')
            orphan.mkdir()
            patches = [
                mock.patch.object(broker, 'acquire_project_lock', return_value=123),
                mock.patch.object(broker, 'recover_runtime_slices', side_effect=lambda: events.append('recover')),
                mock.patch.object(broker, 'remove_recovered_session_directories',
                                  side_effect=lambda path: events.append('prune') or shutil.rmtree(orphan)),
                mock.patch.object(broker, 'configure_slice', side_effect=lambda *args: events.append('configure')),
                mock.patch.object(broker, 'Broker', side_effect=lambda *args, **kwargs: events.append('broker') or mock.Mock()),
                mock.patch.object(broker, 'stop_project_slice'),
                mock.patch.object(broker, 'release_project_lock'),
                mock.patch.object(broker.signal, 'signal'),
                mock.patch.object(broker.os, 'chmod'),
                mock.patch.object(broker.os, 'unlink'),
                mock.patch.object(broker, 'UnixServer'),
            ]
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9], patches[10] as server_class:
                server_class.return_value.serve_forever.side_effect = lambda **kwargs: events.append('serve')
                broker.serve(str(pathlib.Path(td) / 'broker.sock'), root, aggregate_limits=True)
            self.assertLess(events.index('recover'), events.index('prune'))
            self.assertLess(events.index('prune'), events.index('configure'))
            self.assertLess(events.index('configure'), events.index('broker'))
            self.assertFalse(orphan.exists())

    def test_aggregate_startup_holds_lock_while_recovering_and_configuring(self):
        events = []
        lock_fd = 123
        with mock.patch.object(broker, 'acquire_project_lock', side_effect=lambda: events.append('lock') or lock_fd), \
             mock.patch.object(broker, 'recover_runtime_slices', side_effect=lambda: events.append('recover')), \
             mock.patch.object(broker, 'configure_slice', side_effect=lambda *args: events.append('configure')), \
             mock.patch.object(broker, 'Broker', side_effect=lambda *args, **kwargs: events.append('broker') or mock.Mock()), \
             mock.patch.object(broker, 'stop_project_slice', side_effect=lambda: events.append('stop')), \
             mock.patch.object(broker, 'release_project_lock', side_effect=lambda fd: events.append(('release', fd))), \
             mock.patch.object(broker.signal, 'signal'), \
             mock.patch.object(broker.os, 'chmod'), \
             mock.patch.object(broker.os, 'unlink'), \
             mock.patch.object(broker, 'UnixServer') as server_class:
            server_class.return_value.serve_forever.side_effect = lambda **kwargs: events.append('serve')
            broker.serve('/tmp/orbit-test.sock', '/tmp/orbit-test-root', aggregate_limits=True)
        self.assertEqual(events[:4], ['lock', 'recover', 'configure', 'broker'])
        self.assertIn('serve', events)
        self.assertLess(events.index('stop'), events.index(('release', lock_fd)))

    def test_failed_recovery_does_not_revert_project_slice_or_leave_lock_held(self):
        events = []
        with mock.patch.object(broker, 'acquire_project_lock', side_effect=lambda: events.append('lock') or 99), \
             mock.patch.object(broker, 'recover_runtime_slices', side_effect=RuntimeError('inventory failed')), \
             mock.patch.object(broker, 'configure_slice', side_effect=lambda *args: events.append('configure')), \
             mock.patch.object(broker, 'stop_project_slice', side_effect=lambda: events.append('stop')), \
             mock.patch.object(broker, 'release_project_lock', side_effect=lambda fd: events.append(('release', fd))), \
             mock.patch.object(broker, 'UnixServer'), \
             mock.patch.object(broker.signal, 'signal'):
            with self.assertRaisesRegex(RuntimeError, 'inventory failed'):
                broker.serve('/tmp/orbit-test.sock', '/tmp/orbit-test-root', aggregate_limits=True)
        self.assertEqual(events, ['lock', ('release', 99)])

    def test_project_lock_excludes_second_owner_and_releases_on_close(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-lock-') as td:
            lock_path = pathlib.Path(td) / 'runtime' / 'orbit-any-app' / 'aggregate.lock'
            first = broker.acquire_project_lock(lock_path)
            try:
                self.assertEqual(pathlib.Path(lock_path).stat().st_mode & 0o777, 0o600)
                with self.assertRaisesRegex(RuntimeError, 'already owns'):
                    broker.acquire_project_lock(lock_path)
            finally:
                broker.release_project_lock(first)
            second = broker.acquire_project_lock(lock_path)
            broker.release_project_lock(second)

    def test_project_flock_is_released_by_owner_process_death(self):
        import subprocess
        import time
        with tempfile.TemporaryDirectory(prefix='orbit-broker-lock-death-') as td:
            lock_path = pathlib.Path(td) / 'runtime' / 'orbit-any-app' / 'aggregate.lock'
            child = subprocess.Popen([sys.executable, '-c',
                'import sys,time; sys.path.insert(0, sys.argv[1]); import broker; '
                'fd=broker.acquire_project_lock(sys.argv[2]); print("locked", flush=True); time.sleep(30)',
                str(ROOT / 'any_app_broker'), str(lock_path)], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), 'locked')
                with self.assertRaisesRegex(RuntimeError, 'already owns'):
                    broker.acquire_project_lock(lock_path)
                child.kill()
                child.wait(timeout=2)
                fd = broker.acquire_project_lock(lock_path)
                broker.release_project_lock(fd)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=2)
                child.stdout.close()

    def test_orphan_recovery_only_reverts_exact_owned_runtime_slices(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-recovery-') as td:
            runtime = pathlib.Path(td)
            control = runtime / 'systemd' / 'user.control'
            (control / ('orbit-any-app-' + 'a' * 32 + '.slice.d')).mkdir(parents=True)
            (control / 'unrelated.slice.d').mkdir()
            (control / ('orbit-any-app-' + 'b' * 31 + '.slice.d')).mkdir()
            listing = broker.subprocess.CompletedProcess([], 0,
                'orbit-any-app.slice loaded active active\n'
                'orbit-any-app-' + 'd' * 32 + '.slice loaded inactive dead\n'
                'unrelated.slice loaded active running\n', '')
            calls = []
            cgroup_root = runtime / 'cgroup'
            empty_slice = 'orbit-any-app.slice'
            empty_group = cgroup_root / empty_slice
            empty_group.mkdir(parents=True)
            (empty_group / 'cgroup.events').write_text('populated 0\\nfrozen 0\\n')
            def run(command, **kwargs):
                calls.append(command)
                if command[2] == 'list-units': return listing
                if command[2] == 'show':
                    return broker.subprocess.CompletedProcess(command, 0,
                        f'ControlGroup=/{command[3]}\\n', '')
                if command[2] == 'revert':
                    (control / (command[-1] + '.d')).rmdir() if (control / (command[-1] + '.d')).exists() else None
                return broker.subprocess.CompletedProcess(command, 0, '', '')
            with mock.patch.object(broker, '_slice_cgroup_unpopulated', return_value=True), \
                 mock.patch.object(broker.subprocess, 'run', side_effect=run):
                recovered = broker.recover_runtime_slices('systemctl', runtime)
            self.assertEqual(set(recovered), {
                'orbit-any-app.slice',
                'orbit-any-app-' + 'a' * 32 + '.slice',
                'orbit-any-app-' + 'd' * 32 + '.slice'})
            actions = [(cmd[2], cmd[-1]) for cmd in calls[1:]]
            for unit in recovered:
                self.assertIn(('revert', unit), actions)
                self.assertIn(('stop', unit), actions)
            self.assertFalse(any(unit == 'unrelated.slice' for _, unit in actions))

    def test_active_but_empty_slice_is_recovered_only_after_cgroup_proof(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-empty-slice-') as td:
            root = pathlib.Path(td)
            group = root / 'user.slice' / 'orbit-any-app.slice'
            group.mkdir(parents=True)
            events = group / 'cgroup.events'
            events.write_text('populated 0\nfrozen 0\n')
            command = ['systemctl', '--user', 'show', 'orbit-any-app.slice',
                       '-p', 'ControlGroup', '--value']
            with mock.patch.object(broker.subprocess, 'run', return_value=
                    broker.subprocess.CompletedProcess(command, 0,
                        '/user.slice/orbit-any-app.slice\n', '')):
                self.assertTrue(broker._slice_cgroup_unpopulated(
                    'orbit-any-app.slice', 'systemctl', root))
            events.write_text('populated 1\nfrozen 0\n')
            with mock.patch.object(broker.subprocess, 'run', return_value=
                    broker.subprocess.CompletedProcess(command, 0,
                        '/user.slice/orbit-any-app.slice\\n', '')):
                self.assertFalse(broker._slice_cgroup_unpopulated(
                    'orbit-any-app.slice', 'systemctl', root))

    def test_slice_recovery_refuses_missing_or_malformed_cgroup_events(self):
        for event_text in ('frozen 0\\n', 'populated 2\\n', 'populated 0 extra\\n'):
            with self.subTest(event_text=event_text), tempfile.TemporaryDirectory(
                    prefix='orbit-broker-cgroup-events-') as td:
                root = pathlib.Path(td)
                group = root / 'user.slice' / 'orbit-any-app.slice'
                group.mkdir(parents=True)
                (group / 'cgroup.events').write_text(event_text)
                command = ['systemctl', '--user', 'show', 'orbit-any-app.slice',
                           '-p', 'ControlGroup', '--value']
                with mock.patch.object(broker.subprocess, 'run', return_value=
                        broker.subprocess.CompletedProcess(command, 0,
                            '/user.slice/orbit-any-app.slice\n', '')):
                    self.assertFalse(broker._slice_cgroup_unpopulated(
                        'orbit-any-app.slice', 'systemctl', root))

    def test_active_slice_recovery_refuses_unproven_populated_subtree(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-active-proof-') as td:
            listing = broker.subprocess.CompletedProcess([], 0,
                'orbit-any-app.slice loaded active running\\n', '')
            with mock.patch.object(broker.subprocess, 'run', return_value=listing), \
                 mock.patch.object(broker, '_slice_cgroup_unpopulated', return_value=False) as prove:
                with self.assertRaisesRegex(RuntimeError, 'active or unverified'):
                    broker.recover_runtime_slices('systemctl', pathlib.Path(td))
            prove.assert_called_once_with('orbit-any-app.slice', 'systemctl')

    def test_orphan_recovery_refuses_unknown_or_failed_slice_state(self):
        for state in ('unknown', 'failed'):
            with self.subTest(state=state), tempfile.TemporaryDirectory(prefix='orbit-broker-recovery-state-') as td:
                listing = broker.subprocess.CompletedProcess([], 0,
                    f'orbit-any-app.slice loaded {state} dead\\n', '')
                with mock.patch.object(broker.subprocess, 'run', return_value=listing) as run:
                    with self.assertRaisesRegex(RuntimeError, 'refusing to recover active'):
                        broker.recover_runtime_slices('systemctl', pathlib.Path(td))
                run.assert_called_once()

    def test_orphan_recovery_refuses_to_stop_active_owned_slice(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-recovery-active-') as td:
            runtime = pathlib.Path(td)
            listing = broker.subprocess.CompletedProcess([], 0,
                'orbit-any-app.slice loaded active running\n', '')
            with mock.patch.object(broker.subprocess, 'run', return_value=listing), \
                 mock.patch.object(broker, '_slice_cgroup_unpopulated', return_value=False) as prove:
                with self.assertRaisesRegex(RuntimeError, 'active or unverified'):
                    broker.recover_runtime_slices('systemctl', runtime)
            prove.assert_called_once_with('orbit-any-app.slice', 'systemctl')

    def test_orphan_recovery_refuses_symlink_owned_dropin(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-recovery-link-') as td:
            runtime = pathlib.Path(td)
            control = runtime / 'systemd' / 'user.control'
            control.mkdir(parents=True)
            link = control / ('orbit-any-app-' + 'e' * 32 + '.slice.d')
            link.symlink_to('/tmp', target_is_directory=True)
            listing = broker.subprocess.CompletedProcess([], 0, '', '')
            with mock.patch.object(broker.subprocess, 'run', return_value=listing) as run:
                with self.assertRaisesRegex(RuntimeError, 'unsafe runtime drop-in'):
                    broker.recover_runtime_slices('systemctl', runtime)
            run.assert_called_once()

    def test_orphan_recovery_fails_closed_if_runtime_revert_fails(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-recovery-fail-') as td:
            runtime = pathlib.Path(td)
            listing = broker.subprocess.CompletedProcess([], 0,
                'orbit-any-app.slice loaded inactive dead\n', '')
            def run(command, **kwargs):
                if command[2] == 'list-units': return listing
                raise broker.subprocess.CalledProcessError(1, command, stderr='denied')
            with mock.patch.object(broker.subprocess, 'run', side_effect=run):
                with self.assertRaises(broker.subprocess.CalledProcessError):
                    broker.recover_runtime_slices('systemctl', runtime)

    def test_startup_failure_includes_bounded_child_stderr(self):
        import os
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b'Failed to join transient scope\n')
            os.close(write_fd); write_fd = -1
            proc = mock.Mock(stderr=mock.Mock(fileno=lambda: read_fd))
            self.assertEqual(broker.startup_failure(proc, 'Xvfb'),
                             'Xvfb exited during startup: Failed to join transient scope')
        finally:
            os.close(read_fd)
            if write_fd >= 0: os.close(write_fd)

    def test_startup_failure_stderr_read_does_not_wait_for_inherited_pipe_eof(self):
        import os
        import time
        read_fd, write_fd = os.pipe()
        try:
            proc = mock.Mock(stderr=mock.Mock(fileno=lambda: read_fd))
            started = time.monotonic()
            self.assertEqual(broker.startup_failure(proc, 'Xvfb'),
                             'Xvfb exited during startup')
            self.assertLess(time.monotonic() - started, 0.3)
        finally:
            os.close(read_fd); os.close(write_fd)

    def test_session_slice_cleanup_reverts_runtime_properties_then_stops_unit(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return broker.subprocess.CompletedProcess(command, 0, '', '')
        with mock.patch.object(broker, 'require_binary', return_value='systemctl'), \
             mock.patch.object(broker.subprocess, 'run', side_effect=run):
            broker.stop_session_slice('a' * 32)
        self.assertEqual([command[2] for command in calls], ['revert', 'stop'])
        self.assertTrue(all(command[-1] == broker.session_slice('a' * 32)
                            for command in calls))

    def test_project_slice_cleanup_reverts_runtime_properties_then_stops_unit(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return broker.subprocess.CompletedProcess(command, 0, '', '')
        with mock.patch.object(broker, 'require_binary', return_value='systemctl'), \
             mock.patch.object(broker.subprocess, 'run', side_effect=run):
            broker.stop_project_slice()
        self.assertEqual([command[2] for command in calls], ['revert', 'stop'])
        self.assertTrue(all(command[-1] == broker.PROJECT_SLICE for command in calls))

    def test_aggregate_setup_failure_stops_partially_created_slice(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-cgroup-fail-') as td:
            with mock.patch.object(broker, 'configure_slice'):
                instance = broker.Broker(pathlib.Path(td) / 'sessions', aggregate_limits=True)
            with mock.patch.object(broker, 'configure_slice'), \
                 mock.patch.object(broker, 'require_binary', return_value='xauth'), \
                 mock.patch.object(broker.subprocess, 'run',
                                   side_effect=broker.subprocess.CalledProcessError(1, ['xauth'])), \
                 mock.patch.object(broker, 'stop_session_slice') as stop:
                with self.assertRaises(broker.subprocess.CalledProcessError):
                    instance.create()
            stop.assert_called_once()
            self.assertEqual(instance.displays, set())
            self.assertEqual(list(instance.root.iterdir()), [])

    def test_aggregate_limits_are_opt_in_until_live_validation(self):
        with mock.patch.object(broker, 'serve') as serve:
            self.assertEqual(broker.main(['serve']), 0)
            self.assertFalse(serve.call_args.kwargs['aggregate_limits'])
            broker.main(['--aggregate-limits', 'serve'])
            self.assertTrue(serve.call_args.kwargs['aggregate_limits'])
            self.assertIsNone(serve.call_args.kwargs['owner_unit'])
            broker.main(['--aggregate-limits', '--owner-unit',
                         'orbit-any-app-broker-' + 'a' * 32 + '.service', 'serve'])
            self.assertEqual(serve.call_args.kwargs['owner_unit'],
                             'orbit-any-app-broker-' + 'a' * 32 + '.service')

    def test_session_slice_name_requires_a_generated_uuid(self):
        self.assertEqual(broker.session_slice('a' * 32), 'orbit-any-app-' + 'a' * 32 + '.slice')
        for invalid in ('sid', '../evil', 'A' * 32):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'invalid session id'):
                broker.session_slice(invalid)

    def test_broker_owner_unit_is_strictly_uuid_named(self):
        valid = 'orbit-any-app-broker-' + 'a' * 32 + '.service'
        self.assertEqual(broker.broker_owner_unit(valid), valid)
        for invalid in (None, 3, 'orbit-any-app.slice', 'orbit-any-app-broker-' + 'A' * 32 + '.service',
                        'orbit-any-app-broker-' + 'a' * 32 + '.scope',
                        'orbit-any-app-broker-' + 'a' * 32 + '.service.evil',
                        'orbit-any-app-broker-' + 'a' * 31 + '.service'):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'invalid broker owner'):
                broker.broker_owner_unit(invalid)

    def test_scoped_command_constructs_exact_optional_owner_dependencies(self):
        sid = 'b' * 32
        owner = 'orbit-any-app-broker-' + 'a' * 32 + '.service'
        cmd = ['sleep', '1']
        with mock.patch.object(broker, 'require_binary', return_value='/bin/systemd-run'):
            self.assertEqual(broker.scoped_command(sid, cmd), [
                '/bin/systemd-run', '--user', '--scope', '--slice', broker.session_slice(sid), '--', *cmd])
            self.assertEqual(broker.scoped_command(sid, cmd, owner_unit=owner), [
                '/bin/systemd-run', '--user', '--scope', '--slice', broker.session_slice(sid),
                f'--property=BindsTo={owner}', f'--property=After={owner}', '--', *cmd])
            self.assertEqual(broker.scoped_command(sid, cmd, enabled=False), cmd)
            with self.assertRaisesRegex(ValueError, 'requires aggregate'):
                broker.scoped_command(sid, cmd, enabled=False, owner_unit=owner)

    def test_scoped_command_rejects_bad_owner_before_launch(self):
        with mock.patch.object(broker, 'require_binary', return_value='/bin/systemd-run'):
            with self.assertRaisesRegex(ValueError, 'invalid broker owner'):
                broker.scoped_command('c' * 32, ['true'], owner_unit='orbit-any-app.slice')

    def test_runtime_observe_and_input_scopes_carry_explicit_broker_owner(self):
        from types import SimpleNamespace
        owner = 'orbit-any-app-broker-' + 'd' * 32 + '.service'
        completed = broker.subprocess.CompletedProcess([], 0, b'png', b'')

        observe_session = SimpleNamespace(
            deadline=broker.DispatchDeadline(1), id='b' * 32,
            broker=SimpleNamespace(owner_unit=owner, keep_evidence=False), env={},
            frames=broker.collections.OrderedDict(), revision=0, touch=lambda: None)
        observe_scope = mock.Mock(side_effect=lambda sid, cmd, **kwargs: cmd)
        with mock.patch.object(broker, 'require_binary', return_value='import'), \
             mock.patch.object(broker, 'limited_command', side_effect=lambda cmd: cmd), \
             mock.patch.object(broker, 'scoped_command', observe_scope), \
             mock.patch.object(observe_session.deadline, 'run', return_value=completed):
            result = broker.Session.execute(observe_session, {'op': 'observe'})
        self.assertEqual(result['frame_id'] in observe_session.frames, True)
        self.assertEqual(observe_scope.call_args.kwargs['owner_unit'], owner)

        input_session = SimpleNamespace(
            deadline=broker.DispatchDeadline(1), id='b' * 32, env={}, revision=4,
            frames={'frame': 4}, broker=SimpleNamespace(owner_unit=owner),
            touch=lambda: None)
        input_scope = mock.Mock(side_effect=lambda sid, cmd, **kwargs: cmd)
        with mock.patch.object(broker, 'require_binary', return_value='xdotool'), \
             mock.patch.object(broker, 'limited_command', side_effect=lambda cmd: cmd), \
             mock.patch.object(broker, 'scoped_command', input_scope), \
             mock.patch.object(input_session.deadline, 'run', return_value=completed):
            broker.Session.execute(input_session, {'op': 'key', 'frame_id': 'frame', 'key': 'Return'})
        self.assertEqual(input_scope.call_args.kwargs['owner_unit'], owner)

    def test_owner_verification_fails_closed_on_manager_command_errors(self):
        unit = 'orbit-any-app-broker-' + 'a' * 32 + '.service'
        with mock.patch.object(broker, 'require_binary', return_value='systemctl'), \
             mock.patch.object(broker.subprocess, 'run',
                               side_effect=broker.subprocess.TimeoutExpired(['systemctl'], 3)):
            with self.assertRaisesRegex(RuntimeError, 'cannot verify broker owner'):
                broker.verify_broker_owner(unit, pid=4321)
        with mock.patch.object(broker, 'require_binary', return_value='systemctl'), \
             mock.patch.object(broker.subprocess, 'run',
                               side_effect=broker.subprocess.CalledProcessError(1, ['systemctl'])):
            with self.assertRaisesRegex(RuntimeError, 'cannot verify broker owner'):
                broker.verify_broker_owner(unit, pid=4321)

    def test_owner_verification_requires_current_active_mainpid_and_invocation(self):
        unit = 'orbit-any-app-broker-' + 'a' * 32 + '.service'
        output = 'ActiveState=active\nMainPID=4321\nInvocationID=' + 'b' * 32 + '\n'
        completed = broker.subprocess.CompletedProcess([], 0, output, '')
        with (mock.patch.object(broker, 'require_binary', return_value='systemctl'),
              mock.patch.object(broker.subprocess, 'run', return_value=completed) as run):
            self.assertEqual(broker.verify_broker_owner(unit, pid=4321), unit)
        self.assertEqual(run.call_args.args[0][3], unit)
        for bad in ('ActiveState=inactive\nMainPID=4321\nInvocationID=' + 'b' * 32 + '\n',
                    'ActiveState=active\nMainPID=9999\nInvocationID=' + 'b' * 32 + '\n',
                    'ActiveState=active\nMainPID=4321\nInvocationID=\n'):
            completed = broker.subprocess.CompletedProcess([], 0, bad, '')
            with (mock.patch.object(broker, 'require_binary', return_value='systemctl'),
                  mock.patch.object(broker.subprocess, 'run', return_value=completed)):
                with self.assertRaisesRegex(RuntimeError, 'not the active MainPID'):
                    broker.verify_broker_owner(unit, pid=4321)

    def test_aggregate_serve_verifies_owner_before_mutating_project(self):
        unit = 'orbit-any-app-broker-' + 'a' * 32 + '.service'
        events = []
        patches = (
            mock.patch.object(broker, 'verify_broker_owner', side_effect=lambda u: events.append('owner') or u),
            mock.patch.object(broker, 'acquire_project_lock', side_effect=lambda: events.append('lock') or 7),
            mock.patch.object(broker, 'recover_runtime_slices', side_effect=lambda: events.append('recover')),
            mock.patch.object(broker, 'remove_recovered_session_directories', side_effect=lambda p: events.append('prune')),
            mock.patch.object(broker, 'configure_slice', side_effect=lambda *a: events.append('configure')),
            mock.patch.object(broker, 'Broker', side_effect=lambda *a, **kw: events.append(('broker', kw['owner_unit'])) or mock.Mock()),
            mock.patch.object(broker, 'stop_project_slice'),
            mock.patch.object(broker, 'release_project_lock'),
            mock.patch.object(broker.signal, 'signal'),
            mock.patch.object(broker.os, 'chmod'),
            mock.patch.object(broker.os, 'unlink'),
            mock.patch.object(broker, 'UnixServer'),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
                patches[6], patches[7], patches[8], patches[9], patches[10], patches[11] as server_class:
            server_class.return_value.serve_forever.side_effect = lambda **kw: events.append('serve')
            broker.serve('/tmp/orbit-test.sock', '/tmp/orbit-test-root',
                         aggregate_limits=True, owner_unit=unit)
        self.assertEqual(events[0:2], ['owner', 'lock'])
        self.assertIn(('broker', unit), events)

    def test_invalid_owner_aborts_before_project_lock_or_mutation(self):
        events = []
        with mock.patch.object(broker, 'verify_broker_owner', side_effect=RuntimeError('owner mismatch')), \
             mock.patch.object(broker, 'acquire_project_lock', side_effect=lambda: events.append('lock')):
            with self.assertRaisesRegex(RuntimeError, 'owner mismatch'):
                broker.serve('/tmp/not-created.sock', '/tmp/not-created-root',
                             aggregate_limits=True,
                             owner_unit='orbit-any-app-broker-' + 'a' * 32 + '.service')
        self.assertEqual(events, [])

    def test_owner_unit_requires_aggregate_mode(self):
        unit = 'orbit-any-app-broker-' + 'a' * 32 + '.service'
        with self.assertRaisesRegex(ValueError, 'requires aggregate'):
            broker.Broker('/tmp/orbit-not-created', aggregate_limits=False, owner_unit=unit)

    def test_slice_limit_readback_detects_missing_or_unapplied_limits(self):
        good = 'MemoryMax=3221225472\nTasksMax=256\nCPUQuotaPerSecUSec=4s\n'
        completed = broker.subprocess.CompletedProcess([], 0, good, '')
        with mock.patch.object(broker, 'require_binary', return_value='systemctl'), \
             mock.patch.object(broker.subprocess, 'run', side_effect=[completed, completed]) as run:
            broker.configure_slice('orbit-any-app.slice', '3G', '256', '400%')
        self.assertEqual(run.call_count, 2)
        bad = broker.subprocess.CompletedProcess([], 0,
              'MemoryMax=0\nTasksMax=256\nCPUQuotaPerSecUSec=4s\n', '')
        with mock.patch.object(broker, 'require_binary', return_value='systemctl'), \
             mock.patch.object(broker.subprocess, 'run', side_effect=[completed, bad]):
            with self.assertRaisesRegex(RuntimeError, 'did not apply'):
                broker.configure_slice('orbit-any-app.slice', '3G', '256', '400%')


class ChildResourceLimitTests(unittest.TestCase):
    def test_limited_child_observes_cpu_memory_file_and_fd_ceilings(self):
        import json
        import resource
        command = broker.limited_command([sys.executable, '-c',
            'import json,resource; print(json.dumps({'
            '"as":resource.getrlimit(resource.RLIMIT_AS),'
            '"cpu":resource.getrlimit(resource.RLIMIT_CPU),'
            '"fsize":resource.getrlimit(resource.RLIMIT_FSIZE),'
            '"nofile":resource.getrlimit(resource.RLIMIT_NOFILE)}))'])
        completed = broker.subprocess.run(command, capture_output=True, text=True,
                                          check=True, timeout=5)
        limits = json.loads(completed.stdout)
        self.assertEqual(limits['as'], [broker.CHILD_AS_LIMIT, broker.CHILD_AS_LIMIT])
        self.assertEqual(limits['cpu'], [broker.CHILD_CPU_SOFT_LIMIT,
                                         broker.CHILD_CPU_HARD_LIMIT])
        self.assertEqual(limits['fsize'], [broker.CHILD_FSIZE_LIMIT,
                                           broker.CHILD_FSIZE_LIMIT])
        self.assertEqual(limits['nofile'], [broker.CHILD_NOFILE_LIMIT,
                                            broker.CHILD_NOFILE_LIMIT])


class ProcessGroupCleanupTests(unittest.TestCase):
    def test_cleanup_kills_descendant_even_after_group_leader_exits(self):
        import os
        import subprocess
        import time

        with tempfile.TemporaryDirectory(prefix='orbit-broker-descendants-') as td:
            td = pathlib.Path(td)
            child_pidfile = td / 'child.pid'
            marker = td / 'alive'
            child_code = (
                "import os,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"open({str(child_pidfile)!r}, 'w').write(str(os.getpid())); "
                f"p={str(marker)!r}; "
                "\nwhile True:\n open(p, 'a').write('x'); time.sleep(.02)"
            )
            parent_code = (
                'import subprocess,sys,time; '
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
                f"deadline=time.monotonic()+2; p={str(child_pidfile)!r}; "
                "\nwhile not __import__('os').path.exists(p) and time.monotonic()<deadline: time.sleep(.01)"
            )
            leader = subprocess.Popen([sys.executable, '-c', parent_code],
                                      start_new_session=True)
            leader.wait(timeout=3)
            self.assertTrue(child_pidfile.exists(), 'descendant did not start')
            child_pid = int(child_pidfile.read_text())
            self.assertTrue(marker.exists(), 'descendant did not execute')
            broker.terminate_process_group(leader, term_grace=0.05, kill_grace=0.5)
            size_after_cleanup = marker.stat().st_size
            time.sleep(0.1)
            self.assertEqual(marker.stat().st_size, size_after_cleanup,
                             'descendant continued executing after group cleanup')
            # Linux may retain an orphaned zombie briefly; either absence or Z is
            # acceptable, but a live process after bounded cleanup is not.
            proc_stat = pathlib.Path(f'/proc/{child_pid}/stat')
            if proc_stat.exists():
                fields = proc_stat.read_text().split()
                self.assertEqual(fields[2], 'Z', 'descendant remains live')


class EvidenceBudgetTests(unittest.TestCase):
    def test_global_evidence_byte_budget_prunes_oldest_frame_and_preserves_latest(self):
        import os
        import time

        with tempfile.TemporaryDirectory(prefix='orbit-broker-evidence-') as td:
            root = pathlib.Path(td)
            instance = broker.Broker(root / 'sessions', root / 'evidence')
            old = b'old-frame'
            new = b'new-frame'
            with mock.patch.object(broker, 'MAX_EVIDENCE_BYTES', len(old) + len(new)):
                instance.store_evidence('session-a', 'frame-0001.png', old)
            old_path = root / 'evidence/session-a/frame-0001.png'
            self.assertEqual(old_path.stat().st_mode & 0o777, 0o600,
                             'persisted screenshot must be owner-only')
            os.utime(old_path, ns=(1, 1))
            with mock.patch.object(broker, 'MAX_EVIDENCE_BYTES', len(new)):
                instance.store_evidence('session-b', 'frame-0001.png', new)
            self.assertFalse(old_path.exists(), 'oldest evidence should be pruned')
            self.assertEqual((root / 'evidence/session-b/frame-0001.png').read_bytes(), new)

    def test_single_frame_larger_than_budget_is_rejected_and_not_left_behind(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-evidence-') as td:
            root = pathlib.Path(td)
            instance = broker.Broker(root / 'sessions', root / 'evidence')
            with mock.patch.object(broker, 'MAX_EVIDENCE_BYTES', 3):
                with self.assertRaisesRegex(RuntimeError, 'exceeds configured storage budget'):
                    instance.store_evidence('session-a', 'frame-0001.png', b'too-large')
            self.assertFalse((root / 'evidence/session-a/frame-0001.png').exists())
            self.assertFalse((root / 'evidence/session-a/frame-0001.png.tmp').exists())

    def test_concurrent_stores_never_exceed_global_budget_or_leave_temp_files(self):
        import threading

        with tempfile.TemporaryDirectory(prefix='orbit-broker-evidence-') as td:
            root = pathlib.Path(td)
            instance = broker.Broker(root / 'sessions', root / 'evidence')
            failures = []
            def save(index):
                try:
                    instance.store_evidence(f'session-{index % 4}',
                                            f'frame-{index:04d}.png', b'x' * 10)
                except Exception as exc:
                    failures.append(exc)
            with mock.patch.object(broker, 'MAX_EVIDENCE_BYTES', 45):
                threads = [threading.Thread(target=save, args=(i,)) for i in range(20)]
                for thread in threads: thread.start()
                for thread in threads: thread.join(timeout=3)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(failures, [])
            pngs = list((root / 'evidence').glob('*/*.png'))
            self.assertLessEqual(sum(path.stat().st_size for path in pngs), 45)
            self.assertEqual(list((root / 'evidence').glob('*/*.tmp')), [])

    def test_filesystem_prune_failure_does_not_leave_new_frame_or_temp_file(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-evidence-') as td:
            root = pathlib.Path(td)
            instance = broker.Broker(root / 'sessions', root / 'evidence')
            with mock.patch.object(instance, '_prune_evidence', side_effect=OSError('disk failure')):
                with self.assertRaisesRegex(OSError, 'disk failure'):
                    instance.store_evidence('session-a', 'frame-0001.png', b'frame')
            self.assertFalse((root / 'evidence/session-a/frame-0001.png').exists())
            self.assertFalse((root / 'evidence/session-a/frame-0001.png.tmp').exists())

    def test_prune_reclaims_empty_session_directories(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-evidence-') as td:
            root = pathlib.Path(td)
            instance = broker.Broker(root / 'sessions', root / 'evidence')
            instance.store_evidence('session-a', 'frame-0001.png', b'frame')
            with mock.patch.object(broker, 'MAX_EVIDENCE_BYTES', 0):
                instance.delete_evidence('session-a', 'frame-0001.png')
                with instance.evidence_lock:
                    instance._prune_evidence()
            self.assertFalse((root / 'evidence/session-a').exists())

    def test_broker_restart_removes_crash_left_temporary_frames(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-evidence-restart-') as td:
            root = pathlib.Path(td)
            evidence = root / 'evidence'
            stale_dir = evidence / 'session-crashed'
            stale_dir.mkdir(parents=True, mode=0o700)
            stale_temp = stale_dir / 'frame-0007.png.tmp'
            stale_temp.write_bytes(b'partial-frame')
            valid = stale_dir / 'frame-0006.png'
            valid.write_bytes(b'prior-frame')
            instance = broker.Broker(root / 'sessions', evidence)
            self.assertFalse(stale_temp.exists(), 'restart should remove partial writes')
            self.assertEqual(valid.read_bytes(), b'prior-frame', 'valid evidence should be preserved')
            # A second initialization is idempotent once stale artifacts are gone.
            broker.Broker(root / 'sessions', evidence)
            self.assertFalse(stale_temp.exists())
            self.assertEqual(valid.read_bytes(), b'prior-frame')

    def test_broker_restart_enforces_budget_on_existing_evidence(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-evidence-reconcile-') as td:
            root = pathlib.Path(td)
            evidence = root / 'evidence'
            evidence.mkdir()
            old_dir, new_dir = evidence / 'old', evidence / 'new'
            old_dir.mkdir()
            new_dir.mkdir()
            old_file, new_file = old_dir / 'frame.png', new_dir / 'frame.png'
            old_file.write_bytes(b'old')
            new_file.write_bytes(b'new')
            import os
            os.utime(old_file, ns=(1, 1))
            os.utime(new_file, ns=(2, 2))
            with mock.patch.object(broker, 'MAX_EVIDENCE_BYTES', len(b'new')):
                broker.Broker(root / 'sessions', evidence)
            self.assertFalse(old_file.exists(), 'restart must prune oldest persisted frame')
            self.assertTrue(new_file.exists(), 'restart must preserve newest persisted frame')


class IdleReaperTests(unittest.TestCase):
    def test_expired_session_is_closed_and_capability_and_display_released(self):
        with tempfile.TemporaryDirectory(prefix='orbit-broker-reap-') as td:
            instance = broker.Broker(pathlib.Path(td) / 'sessions')
            session = mock.Mock(last=broker.now() - broker.IDLE_TTL - 1,
                                display=':97', close=mock.Mock())
            instance.sessions['expired'] = session
            instance.tokens['expired'] = 'secret'
            instance.displays.add(97)
            instance.reap()
            session.close.assert_called_once_with()
            self.assertNotIn('expired', instance.sessions)
            self.assertNotIn('expired', instance.tokens)
            self.assertNotIn(97, instance.displays)
