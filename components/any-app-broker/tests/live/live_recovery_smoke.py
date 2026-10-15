#!/usr/bin/env python3
"""Desktop integration check: real orphan recovery followed by aggregate GUI smoke.

Run only on the disposable Orbit Desktop from the project root, in an environment
with the GUI tools on PATH. Creates unique runtime-only user-slice fixtures and
removes them in finally blocks. It does not change system configuration.
"""
import os
import pathlib
import subprocess
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'any_app_broker'))
import broker  # noqa: E402

SYSTEMCTL = ['systemctl', '--user']
RUNTIME = pathlib.Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
CONTROL = RUNTIME / 'systemd' / 'user.control'


def run(args, *, check=True, timeout=10):
    return subprocess.run(args, check=check, capture_output=True, text=True,
                          timeout=timeout)


def main():
    orphan = broker.session_slice(uuid.uuid4().hex)
    orphan_dropin = CONTROL / f'{orphan}.d'
    sentinel = f'orbit-recovery-sentinel-{uuid.uuid4().hex}.slice'
    sentinel_dropin = CONTROL / f'{sentinel}.d'
    if orphan_dropin.exists() or sentinel_dropin.exists():
        raise RuntimeError('refusing to overwrite pre-existing unique fixture')
    try:
        # Simulate an interrupted broker: a runtime property remains on an
        # inactive, valid UUID session slice.
        run(SYSTEMCTL + ['set-property', '--runtime', orphan, 'MemoryMax=512M'])
        run(SYSTEMCTL + ['stop', orphan], check=False)
        state = run(SYSTEMCTL + ['show', orphan, '-p', 'ActiveState', '--value']).stdout.strip()
        if state != 'inactive' or not orphan_dropin.is_dir():
            raise RuntimeError(f'orphan fixture not established: state={state}, path={orphan_dropin}')
        # An unrelated unit is a guard against an overbroad recovery scan.
        run(SYSTEMCTL + ['set-property', '--runtime', sentinel, 'MemoryMax=256M'])
        value = run(SYSTEMCTL + ['show', sentinel, '-p', 'MemoryMax', '--value']).stdout.strip()
        if value != str(256 * 1024 * 1024):
            raise RuntimeError(f'sentinel property did not apply: {value!r}')

        smoke = run([sys.executable, str(ROOT / 'tests' / 'live' / 'smoke.py'), '--aggregate-limits'],
                    timeout=120)
        print(smoke.stdout, end='')
        if smoke.stderr:
            print(smoke.stderr, file=sys.stderr, end='')
        if orphan_dropin.exists():
            raise RuntimeError('aggregate broker startup did not recover the orphan drop-in')
        preserved = run(SYSTEMCTL + ['show', sentinel, '-p', 'MemoryMax', '--value']).stdout.strip()
        if preserved != str(256 * 1024 * 1024) or not sentinel_dropin.is_dir():
            raise RuntimeError(f'aggregate startup changed unrelated sentinel: {preserved!r}')
        print(f'RECOVERY_ACCEPTED orphan={orphan} removed=true unrelated={sentinel} '
              f'preserved_MemoryMax={preserved}')
        return smoke.returncode
    finally:
        # Clean exactly the UUID/sentinel units created above, even on failures.
        for unit in (orphan, sentinel):
            run(SYSTEMCTL + ['revert', '--runtime', unit], check=False, timeout=5)
            run(SYSTEMCTL + ['stop', unit], check=False, timeout=5)
        leftovers = [str(path) for path in (orphan_dropin, sentinel_dropin) if path.exists()]
        if leftovers:
            raise RuntimeError(f'fixture drop-ins remain after cleanup: {leftovers}')
        print('FIXTURES_CLEAN')


if __name__ == '__main__':
    raise SystemExit(main())
