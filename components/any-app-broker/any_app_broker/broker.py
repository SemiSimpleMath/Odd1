#!/usr/bin/env python3
"""Local, capability-token-gated GUI session broker for Stage 1 experiments."""
import argparse
import base64
import collections
import json
import math
import os
import pathlib
import re
import secrets
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import select
import fcntl
import stat

MAX_MESSAGE = 32768
MAX_FRAME = 3 * 1024 * 1024
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_SESSIONS = 3
MAX_WORKERS = 16
IDLE_TTL = 600
REQUEST_READ_TIMEOUT = 5
RESPONSE_WRITE_TIMEOUT = 5
DISPATCH_TIMEOUT = 10
# Per-child ceilings enforced by util-linux prlimit(1). These are containment
# guardrails for this disposable broker, not aggregate per-session cgroup quotas.
CHILD_AS_LIMIT = 768 * 1024 * 1024
CHILD_CPU_SOFT_LIMIT = 60
CHILD_CPU_HARD_LIMIT = 65
CHILD_FSIZE_LIMIT = 64 * 1024 * 1024
CHILD_NOFILE_LIMIT = 128
# Aggregate limits are enforced by delegated systemd cgroup-v2 slices on Desktop.
# The parent caps all project children; each session also gets a tighter slice.
PROJECT_SLICE = 'orbit-any-app.slice'
PROJECT_MEMORY_MAX = '3G'
PROJECT_TASKS_MAX = '256'
PROJECT_CPU_QUOTA = '400%'
SESSION_MEMORY_MAX = '1G'
SESSION_TASKS_MAX = '64'
SESSION_CPU_QUOTA = '200%'
SCREEN = '1280x800x24'


def configure_slice(unit, memory_max, tasks_max, cpu_quota):
    """Apply runtime-only aggregate cgroup-v2 limits through the user manager."""
    cmd = [require_binary('systemctl'), '--user', 'set-property', '--runtime', unit,
           f'MemoryMax={memory_max}', f'TasksMax={tasks_max}', f'CPUQuota={cpu_quota}']
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=5)
        observed = subprocess.run([require_binary('systemctl'), '--user', 'show', unit,
                                   '-p', 'MemoryMax', '-p', 'TasksMax',
                                   '-p', 'CPUQuotaPerSecUSec'], check=True,
                                  capture_output=True, text=True, timeout=5)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, 'stderr', None)
        raise RuntimeError(f'cannot configure delegated resource slice {unit}: {detail or exc}') from exc
    values = dict(line.split('=', 1) for line in observed.stdout.splitlines() if '=' in line)
    expected = {'MemoryMax': parse_size(memory_max),
                'TasksMax': int(tasks_max),
                'CPUQuotaPerSecUSec': parse_cpu_quota(cpu_quota)}
    actual = {'MemoryMax': int(values.get('MemoryMax', '-1')),
              'TasksMax': int(values.get('TasksMax', '-1')),
              'CPUQuotaPerSecUSec': parse_usec(values.get('CPUQuotaPerSecUSec', '0'))}
    if actual != expected:
        raise RuntimeError(f'resource slice {unit} limits did not apply: {values!r}')


def parse_size(value):
    units = {'K': 1024, 'M': 1024**2, 'G': 1024**3}
    return int(value[:-1]) * units[value[-1]] if value[-1] in units else int(value)


def parse_cpu_quota(value):
    return int(float(value.rstrip('%')) * 10_000)


def parse_usec(value):
    if value.endswith('ms'):
        return int(float(value[:-2]) * 1000)
    if value.endswith('us'):
        return int(float(value[:-2]))
    if value.endswith('s'):
        return int(float(value[:-1]) * 1_000_000)
    return int(value)


def session_slice(sid):
    if not re.fullmatch(r'[0-9a-f]{32}', sid):
        raise ValueError('invalid session id for resource slice')
    return f'orbit-any-app-{sid}.slice'


def broker_owner_unit(unit):
    """Validate an explicitly supplied, per-run transient broker service unit.

    Never infer lifecycle ownership from ambient process state or accept a broad
    project slice. The UUID-shaped unit contract is deliberately narrower than
    systemd's general unit-name grammar.
    """
    if not isinstance(unit, str) or not re.fullmatch(
            r'orbit-any-app-broker-[0-9a-f]{32}\.service', unit):
        raise ValueError('invalid broker owner service unit')
    return unit


def scoped_command(sid, command, enabled=True, owner_unit=None):
    """Put a broker child in its session cgroup, optionally bound to its owner.

    The owner relationship is opt-in; callers without an authoritative service
    identity retain existing behavior. This helper is not yet wired to CLI/startup.
    """
    if not enabled:
        if owner_unit is not None:
            raise ValueError('broker owner unit requires aggregate scopes')
        return command
    args = [require_binary('systemd-run'), '--user', '--scope', '--slice',
            session_slice(sid)]
    if owner_unit is not None:
        owner_unit = broker_owner_unit(owner_unit)
        args.extend([f'--property=BindsTo={owner_unit}',
                     f'--property=After={owner_unit}'])
    return [*args, '--', *command]


def verify_broker_owner(unit, *, pid=None):
    """Fail closed unless this process is the active MainPID of the named service.

    A syntactically valid UUID unit is not authority by itself. This read-back is
    performed before aggregate startup mutates slices, scratch, or sockets.
    """
    unit = broker_owner_unit(unit)
    systemctl = require_binary('systemctl')
    try:
        result = subprocess.run(
            [systemctl, '--user', 'show', unit, '-p', 'ActiveState', '-p', 'MainPID',
             '-p', 'InvocationID'], check=True, capture_output=True, text=True, timeout=3)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'cannot verify broker owner service {unit}') from exc
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    expected_pid = os.getpid() if pid is None else pid
    if (values.get('ActiveState') != 'active' or
            values.get('MainPID') != str(expected_pid) or
            not re.fullmatch(r'[0-9a-f]{32}', values.get('InvocationID', ''))):
        raise RuntimeError(f'process is not the active MainPID of broker owner {unit}')
    return unit


def limited_command(command):
    """Apply explicit per-process ceilings before execing a broker-owned child."""
    return [require_binary('prlimit'), f'--as={CHILD_AS_LIMIT}',
            f'--cpu={CHILD_CPU_SOFT_LIMIT}:{CHILD_CPU_HARD_LIMIT}',
            f'--fsize={CHILD_FSIZE_LIMIT}', f'--nofile={CHILD_NOFILE_LIMIT}',
            '--', *command]


def startup_failure(proc, name):
    """Read only immediately available bounded stderr; descendants may hold the pipe."""
    detail = ''
    if proc.stderr is not None:
        try:
            fd = proc.stderr.fileno()
            readable, _, _ = select.select([fd], [], [], 0.05)
            if readable:
                detail = os.read(fd, 4096).decode(errors='replace')
        except (OSError, ValueError):
            pass
    detail = ' '.join(detail.strip().split())[:1000]
    return f'{name} exited during startup' + (f': {detail}' if detail else '')


def stop_session_slice(sid):
    """Revert transient runtime properties and stop an experimental session slice."""
    try:
        systemctl = require_binary('systemctl')
        unit = session_slice(sid)
        # `stop` alone leaves --runtime set-property drop-ins behind, so a later
        # use of this unique unit can inherit stale limits. Revert first, then
        # stop the now-unconfigured slice. Both operations are bounded/best effort.
        subprocess.run([systemctl, '--user', 'revert', '--runtime', unit],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=2)
        subprocess.run([systemctl, '--user', 'stop', unit], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=2)
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        pass


def stop_project_slice():
    """Remove runtime-only project limits on orderly broker shutdown."""
    try:
        systemctl = require_binary('systemctl')
        subprocess.run([systemctl, '--user', 'revert', '--runtime', PROJECT_SLICE],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=2)
        subprocess.run([systemctl, '--user', 'stop', PROJECT_SLICE], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=2)
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        pass


def acquire_project_lock(lock_path=None):
    """Exclusively own the shared project slice for one aggregate broker lifetime.

    flock is released by the kernel on process death, unlike a PID/stale-file
    check. The file may remain; its existence alone never means it is locked.
    """
    if lock_path is None:
        runtime = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
        lock_path = pathlib.Path(runtime) / 'orbit-any-app' / 'aggregate-limits.lock'
    lock_path = pathlib.Path(lock_path)
    runtime_root = lock_path.parent.parent
    runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime_root_info = runtime_root.lstat()
    if not stat.S_ISDIR(runtime_root_info.st_mode) or runtime_root_info.st_uid != os.getuid():
        raise RuntimeError('aggregate broker runtime root must be an owner-owned directory')
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_info = lock_path.parent.lstat()
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid():
        raise RuntimeError('aggregate broker lock directory must be an owner-owned directory')
    os.chmod(lock_path.parent, 0o700)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('aggregate broker lock must be an owner-owned regular file')
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('another aggregate-limits broker already owns the project slice') from exc
        return fd
    except Exception:
        os.close(fd)
        raise


def release_project_lock(fd):
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _slice_cgroup_unpopulated(unit, systemctl, cgroup_root=pathlib.Path('/sys/fs/cgroup')):
    """Return True only when cgroup-v2 proves a slice subtree has no processes.

    systemd can report an empty slice as ``active`` simply because the slice
    object is loaded. Recovery may clear runtime properties from that empty
    slice, but must remain fail-closed if ControlGroup or populated state is
    missing, malformed, inaccessible, or populated.
    """
    result = subprocess.run([systemctl, '--user', 'show', unit, '-p', 'ControlGroup',
                             '--value'], check=True, capture_output=True, text=True,
                            timeout=5)
    control_group = result.stdout.strip()
    parts = pathlib.PurePosixPath(control_group).parts
    if (not control_group.startswith('/') or control_group == '/' or
            any(part in ('.', '..') for part in parts) or parts[-1] != unit):
        return False
    root = pathlib.Path(cgroup_root)
    events_path = root.joinpath(*parts[1:], 'cgroup.events')
    try:
        # Events in cgroup v2 are tiny; bound reads and reject unexpected files.
        with events_path.open('rb') as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            return False
        text = raw.decode('ascii')
    except (OSError, UnicodeDecodeError):
        return False
    values = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[0] in values:
            return False
        values[fields[0]] = fields[1]
    return values.get('populated') == '0'


def recover_runtime_slices(systemctl=None, runtime_dir=None):
    """Clear only this app's orphaned runtime slices before aggregate startup.

    A process-wide flock must already be held. Candidate names come from the
    user manager's loaded units and its runtime user.control drop-in directory;
    both are constrained to the exact project/session naming convention.
    """
    systemctl = systemctl or require_binary('systemctl')
    if runtime_dir is None:
        runtime_dir = pathlib.Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
    runtime_dir = pathlib.Path(runtime_dir)
    result = subprocess.run([systemctl, '--user', 'list-units', '--all', '--type=slice',
                             '--plain', '--no-legend'], check=True, capture_output=True,
                            text=True, timeout=5)
    candidates = set()
    active_units = set()
    uncertain_units = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields:
            candidates.add(fields[0])
            # list-units columns are UNIT LOAD ACTIVE SUB DESCRIPTION. Inactive
            # is quiescent; active is further checked against cgroup.events;
            # unknown/failed/other states are never enough to authorize cleanup.
            if len(fields) >= 4 and fields[2] == 'active':
                active_units.add(fields[0])
            elif len(fields) >= 3 and fields[2] != 'inactive':
                uncertain_units.add(fields[0])
    control = runtime_dir / 'systemd' / 'user.control'
    pattern = re.compile(r'orbit-any-app(?:-[0-9a-f]{32})?\.slice\Z')
    try:
        control_info = control.lstat()
        if (not stat.S_ISDIR(control_info.st_mode) or
                control_info.st_uid != os.getuid()):
            raise RuntimeError(f'unsafe systemd user-control path: {control}')
        for path in control.iterdir():
            if path.name.endswith('.slice.d'):
                unit = path.name[:-2]
                if pattern.fullmatch(unit):
                    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid():
                        raise RuntimeError(f'unsafe runtime drop-in entry for {unit}')
                    candidates.add(unit)
    except FileNotFoundError:
        pass
    except NotADirectoryError:
        raise RuntimeError(f'unsafe systemd user-control path: {control}')
    candidates = sorted(unit for unit in candidates if pattern.fullmatch(unit))
    # Slice units may be ``active`` solely because systemd keeps a loaded slice
    # object around even after every process has exited. Permit recovery only
    # when cgroup-v2 explicitly proves the exact subtree is unpopulated; active
    # scopes or units whose state cannot be inspected remain a hard refusal.
    unsafe = sorted(uncertain_units.intersection(candidates))
    unsafe.extend(unit for unit in sorted(active_units.intersection(candidates))
                  if not _slice_cgroup_unpopulated(unit, systemctl))
    if unsafe:
        raise RuntimeError('refusing to recover active or unverified aggregate slice(s): ' + ', '.join(unsafe))
    for unit in candidates:
        subprocess.run([systemctl, '--user', 'revert', '--runtime', unit], check=True,
                       capture_output=True, text=True, timeout=5)
        stopped = subprocess.run([systemctl, '--user', 'stop', unit], check=False,
                                 capture_output=True, text=True, timeout=5)
        if stopped.returncode and 'not loaded' not in (stopped.stderr or '').lower():
            raise RuntimeError(f'could not stop orphaned aggregate slice {unit}: '
                               f'{(stopped.stderr or stopped.stdout).strip()}')
        dropin = control / f'{unit}.d'
        if dropin.exists():
            raise RuntimeError(f'runtime properties remain for orphaned aggregate slice {unit}')
    return candidates


def now(): return time.time()


class DispatchDeadline:
    """One monotonic wall-clock budget shared by a complete broker dispatch."""
    def __init__(self, seconds=DISPATCH_TIMEOUT):
        if (not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or
                not math.isfinite(seconds) or seconds <= 0):
            raise ValueError('dispatch timeout must be a finite positive number')
        self.ends = time.monotonic() + seconds

    def remaining(self):
        left = self.ends - time.monotonic()
        if left <= 0:
            raise TimeoutError('dispatch deadline exceeded')
        return left

    def check(self):
        self.remaining()

    def hold(self, lock):
        """Acquire a contended operation lock only for the remaining dispatch budget."""
        from contextlib import contextmanager

        @contextmanager
        def acquired():
            if not lock.acquire(timeout=self.remaining()):
                raise TimeoutError('dispatch deadline exceeded waiting for broker lock')
            try:
                self.check()
                yield
            finally:
                lock.release()
        return acquired()

    def run(self, command, **kwargs):
        requested = kwargs.get('timeout', self.remaining())
        kwargs['timeout'] = min(requested, self.remaining())
        try:
            return subprocess.run(command, **kwargs)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError('dispatch deadline exceeded during subprocess') from exc

    def sleep(self, seconds):
        left = self.remaining()
        if seconds > left:
            time.sleep(left)
            raise TimeoutError('dispatch deadline exceeded during wait')
        time.sleep(seconds)
        self.check()

def require_binary(name):
    found = shutil.which(name)
    if not found: raise RuntimeError(f'required executable not found: {name}')
    return found


def _process_group_exists(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def terminate_process_group(proc, deadline=None, term_grace=0.25, kill_grace=0.5):
    """Boundedly stop a process group, including descendants after its leader exits.

    All broker-owned commands use start_new_session=True, so their PID is the
    process-group ID. Cleanup is allowed a small fixed grace after dispatch expiry:
    releasing resources takes precedence over returning within the action budget.
    """
    pgid = proc.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        grace = min(term_grace, deadline.remaining()) if deadline else term_grace
    except TimeoutError:
        grace = 0
    end = time.monotonic() + grace
    while _process_group_exists(pgid) and time.monotonic() < end:
        time.sleep(min(0.02, max(0, end - time.monotonic())))
    if _process_group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=kill_grace)
    except (subprocess.TimeoutExpired, TimeoutError):
        pass
    end = time.monotonic() + kill_grace
    while _process_group_exists(pgid) and time.monotonic() < end:
        time.sleep(min(0.02, max(0, end - time.monotonic())))


class Session:
    def __init__(self, broker, sid, path, display, cookie, authfile, deadline):
        self.broker, self.id, self.path = broker, sid, path
        self.display, self.cookie, self.authfile = display, cookie, authfile
        self.deadline = deadline
        self.created = self.last = now()
        self.revision = 0
        self.frames = collections.OrderedDict()
        self.lock = threading.RLock()
        self.procs = []
        # Keep the caller's XDG_RUNTIME_DIR so systemd-run --user can reach the
        # user manager in aggregate mode. Separate X servers/auth/HOME still
        # isolate these smoke sessions; do not point the manager client at path.
        env = dict(os.environ, DISPLAY=display, XAUTHORITY=authfile,
                   HOME=str(path))
        self.env = env
        # Broker.create configures the session slice before running xauth; do not
        # reset that transient unit from inside session initialization. The same
        # template applies child RLIMITs inside each session's aggregate cgroup.
        # Each top-level child and descendants stay scoped.
        # aggregate cgroup. Each top-level child and its descendants stay scoped.
        def session_command(command):
            return scoped_command(sid, limited_command(command),
                                  enabled=self.broker.aggregate_limits,
                                  owner_unit=self.broker.owner_unit)
        xvfb = subprocess.Popen(session_command([require_binary('Xvfb'), display,
                                 '-screen', '0', SCREEN, '-nolisten', 'tcp', '-auth', authfile]),
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                start_new_session=True)
        self.procs.append(xvfb)
        try:
            startup_end = min(time.monotonic() + 8, deadline.ends)
            while time.monotonic() < startup_end:
                deadline.check()
                if xvfb.poll() is not None:
                    raise RuntimeError(startup_failure(xvfb, 'Xvfb'))
                probe = deadline.run(session_command([require_binary('xdpyinfo')]), env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     timeout=1)
                if probe.returncode == 0: break
                deadline.sleep(min(.1, max(0, startup_end - time.monotonic())))
            else:
                deadline.check()
                raise RuntimeError('Xvfb did not become ready')
            state_path = str(path / 'app-state.json')
            app = subprocess.Popen(session_command([sys.executable, broker.app_path,
                                   '--state-file', state_path]),
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   start_new_session=True)
            self.procs.append(app)
            startup_end = min(time.monotonic() + 8, deadline.ends)
            title = 'Orbit Form Smoke'
            while time.monotonic() < startup_end:
                deadline.check()
                if app.poll() is not None: raise RuntimeError(startup_failure(app, 'test app'))
                w = deadline.run(session_command([require_binary('xdotool'), 'search',
                                                 '--onlyvisible', '--name', title]),
                                 env=env, capture_output=True, text=True, timeout=1)
                if w.returncode == 0 and w.stdout.strip():
                    self.window = w.stdout.strip().splitlines()[-1]
                    deadline.run(session_command([require_binary('xdotool'), 'windowraise', self.window]), env=env,
                                 capture_output=True, timeout=2)
                    deadline.check()
                    return
                deadline.sleep(min(.1, max(0, startup_end - time.monotonic())))
            deadline.check()
            raise RuntimeError('Tk app window did not appear')
        except Exception:
            self.close(deadline=deadline)
            raise

    def touch(self): self.last = now()

    def close(self, deadline=None):
        for p in reversed(self.procs):
            terminate_process_group(p, deadline=deadline)
        # stop the transient slice too: this catches any cgroup child not tracked
        # in self.procs, and unloads its runtime unit/properties for the session.
        if self.broker.aggregate_limits:
            stop_session_slice(self.id)
        shutil.rmtree(self.path, ignore_errors=True)


    def execute(self, req):
        deadline = self.deadline
        deadline.check()
        op = req.get('op')
        if op == 'close':
            self.close(deadline=deadline)
            return {'closed': True, 'session_id': self.id}
        if op == 'observe':
            out = deadline.run(scoped_command(self.id, limited_command(
                               [require_binary('import'), '-window', 'root', 'png:-']),
                               owner_unit=self.broker.owner_unit),
                               env=self.env, capture_output=True, timeout=5)
            deadline.check()
            if out.returncode or len(out.stdout) > MAX_FRAME:
                detail = out.stderr.decode(errors='replace').strip()[-1000:]
                raise RuntimeError(
                    f'screen capture failed or exceeded frame-size limit '
                    f'(exit={out.returncode}, bytes={len(out.stdout)}, '
                    f'limit={MAX_FRAME}, stderr={detail!r})')
            fid = secrets.token_urlsafe(12)
            self.revision += 1
            frame_name = f'frame-{self.revision:04d}.png'
            self.frames[fid] = self.revision
            if self.broker.keep_evidence:
                self.broker.store_evidence(self.id, frame_name, out.stdout)
            if len(self.frames) > 16:
                old_id, old_revision = self.frames.popitem(last=False)
                if self.broker.keep_evidence:
                    self.broker.delete_evidence(self.id, f'frame-{old_revision:04d}.png')
            return {'session_id': self.id, 'revision': self.revision,
                    'frame_id': fid, 'timestamp': now(), 'width': 1280, 'height': 800,
                    'mime_type': 'image/png', 'bytes': len(out.stdout),
                    'frame_base64': base64.b64encode(out.stdout).decode('ascii')}
        fid = req.get('frame_id')
        if not fid or fid not in self.frames or self.frames[fid] != self.revision:
            raise ValueError('stale or foreign frame_id; observe before acting')
        if op == 'click':
            x, y = req.get('x'), req.get('y')
            if not isinstance(x, int) or not isinstance(y, int) or not (0 <= x < 1280 and 0 <= y < 800):
                raise ValueError('click coordinates must be in current 1280x800 frame')
            args = ['mousemove', '--sync', str(x), str(y), 'click', '--clearmodifiers', '1']
        elif op == 'type':
            text = req.get('text')
            if not isinstance(text, str) or len(text) > 2000 or '\x00' in text:
                raise ValueError('text must be a NUL-free string of at most 2000 characters')
            args = ['type', '--clearmodifiers', '--delay', '1', '--', text]
        elif op == 'key':
            key = req.get('key')
            if key not in {'Return', 'Escape', 'Tab', 'ctrl+a', 'Ctrl+a', 'Alt+F4', 'space', 'BackSpace'}:
                raise ValueError('key not in the smoke-test allowlist')
            args = ['key', '--clearmodifiers', key]
        elif op == 'wait':
            seconds = req.get('seconds', 0.25)
            if (not isinstance(seconds, (float, int)) or isinstance(seconds, bool) or
                    not math.isfinite(seconds) or seconds < 0 or seconds > 5):
                raise ValueError('wait must be finite and between 0 and 5 seconds')
            deadline.sleep(seconds)
            self.touch()
            return {'session_id': self.id, 'revision': self.revision, 'timestamp': now(), 'waited': seconds}
        else: raise ValueError(f'unsupported operation: {op!r}')
        cmd = scoped_command(self.id, limited_command([require_binary('xdotool'), *args]),
                             owner_unit=self.broker.owner_unit)
        done = deadline.run(cmd, env=self.env, capture_output=True, timeout=5)
        deadline.check()
        if done.returncode: raise RuntimeError('input dispatch failed')
        self.revision += 1
        self.touch()
        return {'session_id': self.id, 'revision': self.revision,
                'timestamp': now(), 'dispatched': op, 'verified': False}


class Broker:
    def __init__(self, root, evidence_root=None, aggregate_limits=False, owner_unit=None):
        self.aggregate_limits = aggregate_limits
        self.owner_unit = broker_owner_unit(owner_unit) if owner_unit is not None else None
        if self.owner_unit is not None and not aggregate_limits:
            raise ValueError('broker owner unit requires aggregate limits')
        self.root = pathlib.Path(root).resolve()
        self.evidence_root = pathlib.Path(evidence_root).resolve() if evidence_root else None
        self.keep_evidence = self.evidence_root is not None
        if self.evidence_root:
            self.evidence_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.evidence_root, 0o700)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.app_path = str(pathlib.Path(__file__).with_name('form_app.py'))
        self.sessions, self.tokens = {}, {}
        self.lock = threading.RLock()
        self.evidence_lock = threading.Lock()
        self.displays = set()
        if self.evidence_root:
            # An interrupted write may leave a non-PNG temporary file. Reconcile
            # those broker-owned artifacts before accepting new writes so crashes
            # cannot silently bypass the evidence byte budget across restarts.
            with self.evidence_lock:
                try:
                    for temporary in self.evidence_root.glob('*/*.tmp'):
                        temporary.unlink(missing_ok=True)
                except OSError as exc:
                    raise RuntimeError('unable to clean temporary screenshot evidence') from exc
                self._prune_evidence()

    def _prune_evidence(self):
        """Keep screenshot evidence bounded by bytes, dropping oldest frames first.

        Call only while holding evidence_lock.
        """
        if not self.evidence_root:
            return
        files = []
        total = 0
        try:
            for path in self.evidence_root.glob('*/*.png'):
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                files.append((stat.st_mtime_ns, path, stat.st_size))
                total += stat.st_size
        except OSError as exc:
            raise RuntimeError('unable to inspect screenshot evidence storage') from exc
        for _, path, size in sorted(files):
            if total <= MAX_EVIDENCE_BYTES:
                break
            try:
                path.unlink()
                total -= size
            except FileNotFoundError:
                total -= size
            except OSError:
                continue
        if total > MAX_EVIDENCE_BYTES:
            raise RuntimeError('unable to enforce screenshot evidence storage budget')
        for directory in self.evidence_root.iterdir():
            if directory.is_dir():
                try:
                    directory.rmdir()
                except OSError:
                    pass

    def delete_evidence(self, sid, frame_name):
        if not self.evidence_root:
            return
        with self.evidence_lock:
            (self.evidence_root / sid / frame_name).unlink(missing_ok=True)

    def store_evidence(self, sid, frame_name, data):
        """Atomically persist a frame and enforce the broker-wide byte ceiling."""
        if not self.evidence_root:
            return
        with self.evidence_lock:
            evidence_dir = self.evidence_root / sid
            evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(evidence_dir, 0o700)
            target = evidence_dir / frame_name
            temporary = evidence_dir / (frame_name + '.tmp')
            try:
                with temporary.open('wb') as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            try:
                self._prune_evidence()
                if not target.exists():
                    raise RuntimeError('screenshot evidence exceeds configured storage budget')
            except Exception:
                # If pruning fails, do not retain the frame that triggered the
                # failed budget enforcement. Older data may remain if the
                # filesystem itself refuses deletion; surface that failure.
                target.unlink(missing_ok=True)
                raise

    def create(self, deadline=None):
        deadline = deadline or DispatchDeadline()
        deadline.check()
        with deadline.hold(self.lock):
            self.reap(deadline)
            if len(self.sessions) >= MAX_SESSIONS: raise RuntimeError('session capacity reached')
            sid, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
            path = pathlib.Path(tempfile.mkdtemp(prefix=f'{sid}-', dir=self.root))
            os.chmod(path, 0o700)
            for n in range(91, 121):
                display = f':{n}'
                if n not in self.displays and not pathlib.Path(f'/tmp/.X11-unix/X{n}').exists():
                    self.displays.add(n); break
            else:
                shutil.rmtree(path, ignore_errors=True)
                raise RuntimeError('no free X display in configured range')
            cookie = secrets.token_hex(16)
            auth = path / 'Xauthority'
            session = None
            try:
                if self.aggregate_limits:
                    configure_slice(session_slice(sid), SESSION_MEMORY_MAX,
                                    SESSION_TASKS_MAX, SESSION_CPU_QUOTA)
                auth_command = limited_command([require_binary('xauth'), '-f', str(auth), 'add', display,
                                                'MIT-MAGIC-COOKIE-1', cookie])
                deadline.run(scoped_command(sid, auth_command,
                                            enabled=self.aggregate_limits,
                                            owner_unit=self.owner_unit), check=True, timeout=3,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                deadline.check()
                session = Session(self, sid, path, display, cookie, str(auth), deadline)
                deadline.check()
            except Exception:
                if session is not None:
                    session.close(deadline=deadline)
                elif self.aggregate_limits:
                    # Slice can exist before Session.__init__ completes (e.g. xauth
                    # or Xvfb startup failure); always remove it on partial setup.
                    stop_session_slice(sid)
                self.displays.discard(n)
                shutil.rmtree(path, ignore_errors=True)
                raise
            self.sessions[sid], self.tokens[sid] = session, token
            return {'session_id': sid, 'capability': token, 'timestamp': now(),
                    'display': display, 'screen': SCREEN, 'expires_idle_seconds': IDLE_TTL}

    def reap(self, deadline=None):
        for sid, session in list(self.sessions.items()):
            if now() - session.last > IDLE_TTL:
                if deadline:
                    session.close(deadline=deadline)
                else:
                    session.close()
                self.displays.discard(int(session.display[1:]))
                self.sessions.pop(sid, None); self.tokens.pop(sid, None)
                if deadline: deadline.check()

    def dispatch(self, req, deadline=None):
        deadline = deadline or DispatchDeadline()
        deadline.check()
        if not isinstance(req, dict): raise ValueError('request must be a JSON object')
        op, sid = req.get('op'), req.get('session_id')
        if op == 'create': return self.create(deadline)
        if not isinstance(sid, str): raise ValueError('session_id is required')
        with deadline.hold(self.lock):
            self.reap(deadline)
            session = self.sessions.get(sid)
            if session is None: raise PermissionError('unknown or expired session')
            if not secrets.compare_digest(str(req.get('capability', '')), self.tokens[sid]):
                raise PermissionError('session capability denied')
        with deadline.hold(session.lock):
            session.deadline = deadline
            session.touch()
            if op == 'close':
                # Closing is a cleanup request: even if waiting for process shutdown
                # consumes the remaining budget, never leave a dead session/token slot.
                try:
                    return session.execute(req)
                finally:
                    with self.lock:
                        self.sessions.pop(sid, None)
                        self.tokens.pop(sid, None)
                        self.displays.discard(int(session.display[1:]))
            result = session.execute(req)
            deadline.check()
            return result

    def shutdown(self):
        with self.lock:
            for session in list(self.sessions.values()): session.close()
            self.sessions.clear(); self.tokens.clear()


class Handler(socketserver.StreamRequestHandler):
    def _reply(self, payload):
        try:
            self.request.settimeout(self.server.response_write_timeout)
            self.wfile.write(json.dumps(payload, separators=(',', ':')).encode() + b'\n')
        except (OSError, socket.timeout):
            pass  # Client disconnected or stopped reading; never pin a worker indefinitely.

    def handle(self):
        # Read with one absolute deadline rather than a per-recv timeout, so a client
        # cannot keep a worker alive forever by trickling bytes just before each timeout.
        deadline = time.monotonic() + self.server.request_read_timeout
        data = bytearray()
        while b'\n' not in data:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._reply({'ok': False, 'error': 'request read deadline exceeded',
                             'error_type': 'TimeoutError'})
                return
            readable, _, _ = select.select([self.request], [], [], remaining)
            if not readable:
                self._reply({'ok': False, 'error': 'request read deadline exceeded',
                             'error_type': 'TimeoutError'})
                return
            chunk = self.request.recv(min(4096, MAX_MESSAGE + 1 - len(data)))
            if not chunk:
                return
            data.extend(chunk)
            if len(data) > MAX_MESSAGE or (b'\n' in data and data.index(b'\n') + 1 > MAX_MESSAGE):
                self._reply({'ok': False, 'error': 'invalid request size',
                             'error_type': 'ValueError'})
                return
        line = bytes(data[:data.index(b'\n') + 1])
        try:
            req = json.loads(line)
            dispatch_deadline = DispatchDeadline()
            result = self.server.broker.dispatch(req, dispatch_deadline)
            dispatch_deadline.check()
            payload = {'ok': True, 'result': result}
        except Exception as e:
            payload = {'ok': False, 'error': str(e), 'error_type': type(e).__name__}
        self._reply(payload)


class UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True
    request_read_timeout = REQUEST_READ_TIMEOUT
    response_write_timeout = RESPONSE_WRITE_TIMEOUT

    def __init__(self, *args, max_workers=MAX_WORKERS, **kwargs):
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError('max_workers must be a positive integer')
        self.max_workers = max_workers
        self._worker_slots = threading.BoundedSemaphore(max_workers)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        # ThreadingMixIn otherwise creates an unbounded daemon thread per accepted
        # connection. Refuse excess clients immediately instead of queueing them.
        if not self._worker_slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(b'{"ok":false,"error":"broker worker capacity reached",'
                                b'"error_type":"BusyError"}\n')
            except (OSError, socket.timeout):
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


def remove_recovered_session_directories(root):
    """Remove only owner-owned scratch dirs left by dead aggregate sessions.

    Called only after the aggregate slice reconciler proves every matching cgroup
    inactive or unpopulated while the broker-wide flock is held. Non-aggregate
    mode deliberately preserves crash residue rather than guessing ownership.
    """
    root = pathlib.Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.getuid():
        raise RuntimeError('session root must be an owner-owned directory')
    os.chmod(root, 0o700)
    pattern = re.compile(r'[0-9a-f]{32}-.+\Z')
    try:
        for path in root.iterdir():
            if not pattern.fullmatch(path.name):
                continue
            if path.is_symlink():
                raise RuntimeError(f'refusing unsafe orphan session path: {path.name}')
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise RuntimeError(f'refusing unsafe orphan session path: {path.name}')
            shutil.rmtree(path)
    except OSError as exc:
        raise RuntimeError('unable to remove recovered session scratch directories') from exc


def serve(sockpath, root, evidence_root=None, aggregate_limits=False, owner_unit=None):
    """Run broker; serialize aggregate ownership and reconcile crash leftovers."""
    if owner_unit is not None:
        if not aggregate_limits:
            raise ValueError('broker owner unit requires aggregate limits')
        owner_unit = verify_broker_owner(owner_unit)
    lock_fd = None
    broker = None
    server = None
    project_setup_started = False
    socket_owned = False
    path = pathlib.Path(sockpath)
    try:
        if aggregate_limits:
            lock_fd = acquire_project_lock()
            recover_runtime_slices()
            remove_recovered_session_directories(root)
            # Only after inventory/recovery succeeds may cleanup mutate the project
            # slice. Set before configure because set-property can partially apply.
            project_setup_started = True
            configure_slice(PROJECT_SLICE, PROJECT_MEMORY_MAX,
                            PROJECT_TASKS_MAX, PROJECT_CPU_QUOTA)
        broker = Broker(root, evidence_root, aggregate_limits=aggregate_limits,
                        owner_unit=owner_unit)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists(): path.unlink()
        server = UnixServer(str(path), Handler)
        socket_owned = True
        server.broker = broker
        os.chmod(path, 0o600)
        def stop(*_):
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
        server.serve_forever(poll_interval=.2)
    finally:
        try:
            if server is not None:
                server.server_close()
            if broker is not None:
                broker.shutdown()
        finally:
            try:
                if aggregate_limits and lock_fd is not None and project_setup_started:
                    stop_project_slice()
                if socket_owned:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
            finally:
                release_project_lock(lock_fd)


def client(sockpath, req):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(15); s.connect(sockpath)
        s.sendall(json.dumps(req, separators=(',', ':')).encode() + b'\n')
        data = bytearray()
        while not data.endswith(b'\n'):
            part = s.recv(65536)
            if not part: break
            data.extend(part)
            if len(data) > MAX_FRAME * 2: raise RuntimeError('broker response exceeded size cap')
    result = json.loads(data)
    if not result.get('ok'):
        print(json.dumps(result, indent=2), file=sys.stderr); return 1
    print(json.dumps(result['result'], indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--socket', default=f'/run/user/{os.getuid()}/orbit-any-app/broker.sock')
    ap.add_argument('--root', default=f'/run/user/{os.getuid()}/orbit-any-app/sessions')
    ap.add_argument('--evidence-root')
    ap.add_argument('--aggregate-limits', action='store_true',
                    help='opt in to experimental runtime systemd cgroup limits')
    ap.add_argument('--owner-unit',
                    help='authoritative transient broker service unit (required with --aggregate-limits)')
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('serve')
    cp = sub.add_parser('request'); cp.add_argument('json')
    args = ap.parse_args(argv)
    if args.command == 'serve':
        serve(args.socket, args.root, args.evidence_root,
              aggregate_limits=args.aggregate_limits, owner_unit=args.owner_unit)
    else:
        return client(args.socket, json.loads(args.json))
    return 0


if __name__ == '__main__':
    sys.exit(main())
