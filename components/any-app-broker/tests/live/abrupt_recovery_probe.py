#!/usr/bin/env python3
"""Orbit Desktop-only destructive SIGKILL/recovery probe with UUID-scoped cleanup."""
import json, os, pathlib, signal, socket, subprocess, sys, tempfile, time, shutil

PROJECT = pathlib.Path(__file__).resolve().parents[2]
BROKER_PY = PROJECT / 'any_app_broker' / 'broker.py'

def main():
    td = pathlib.Path(tempfile.mkdtemp(prefix='orbit-abrupt-recovery-'))
    os.chmod(td, 0o700)
    sock, root, evidence = td/'broker.sock', td/'sessions', td/'evidence'
    command = [sys.executable, str(BROKER_PY), '--socket', str(sock), '--root', str(root),
               '--evidence-root', str(evidence), '--aggregate-limits', 'serve']
    def start():
        proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                start_new_session=True)
        for _ in range(100):
            if proc.poll() is not None: raise RuntimeError('broker failed: '+proc.stderr.read().decode())
            if sock.exists():
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                        probe.settimeout(.2); probe.connect(str(sock))
                        probe.sendall(json.dumps({'op':'__readiness_probe__'}).encode()+bytes([10]))
                        response=bytearray()
                        while not response.endswith(bytes([10])): response.extend(probe.recv(4096))
                    json.loads(response)
                    return proc
                except (OSError, json.JSONDecodeError):
                    pass
            time.sleep(.1)
        proc.kill(); proc.wait(); raise RuntimeError('broker socket timeout')
    def request(op, **kw):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(15); client.connect(str(sock))
            client.sendall(json.dumps(dict(op=op, **kw)).encode()+b'\n')
            data=bytearray()
            while not data.endswith(b'\n'): data.extend(client.recv(65536))
        response=json.loads(data)
        if not response['ok']: raise RuntimeError(response)
        return response['result']
    def owned_pids(home):
        marker=b'HOME='+os.fsencode(home); found=[]
        for proc in pathlib.Path('/proc').iterdir():
            if not proc.name.isdigit(): continue
            try:
                if marker in (proc/'environ').read_bytes().split(b'\0'):
                    fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
                    if fields[0] != 'Z': found.append(int(proc.name))
            except (OSError, PermissionError, IndexError): pass
        return found
    server = restarted = None
    try:
        server=start()
        session=request('create')
        sid=session['session_id']; home=next(root.glob(sid+'-*'))
        pids=owned_pids(home)
        assert pids, 'no uniquely session-owned pids found'
        # Prove the session's Xvfb/app children are actually in its slice before
        # termination, not merely present somewhere in the user manager.
        props=subprocess.run(['systemctl','--user','show',f'orbit-any-app-{sid}.slice',
                              '-p','ControlGroup','--value'],capture_output=True,text=True,check=True,timeout=5)
        cg_prefix=props.stdout.strip()+'/'
        members=[]
        for proc in pathlib.Path('/proc').iterdir():
            if not proc.name.isdigit(): continue
            try:
                cgline=next(x for x in (proc/'cgroup').read_text().splitlines() if x.startswith('0::'))
                if cgline.split('::',1)[1].startswith(cg_prefix): members.append((int(proc.name),(proc/'cmdline').read_bytes()))
            except (OSError, StopIteration, PermissionError): pass
        assert any(b'Xvfb' in cmdline for _,cmdline in members), members
        assert any(b'form_app.py' in cmdline for _,cmdline in members), members
        unit=f'orbit-any-app-{sid}.slice'
        server.kill(); server.wait(timeout=5)
        assert server.returncode == -signal.SIGKILL
        # Characterize the known gap before doing any cleanup: in the current
        # design, killing the broker does not autonomously reap session children.
        # Require at least one original HOME-owned PID to remain so this result
        # cannot be misreported as an automatic cleanup success.
        surviving_pids = sorted(set(owned_pids(home)).intersection(pids))
        assert surviving_pids, ('session children unexpectedly vanished after broker SIGKILL; '
                                'review this probe before changing its expected result')
        # Reap only PIDs freshly observed with this unique session HOME, never a
        # name-based process scan. Refresh avoids killing an already-exited PID.
        for pid in sorted(set(owned_pids(home)).intersection(pids)):
            try: os.kill(pid, signal.SIGKILL)
            except ProcessLookupError: pass
        deadline=time.monotonic()+5
        while owned_pids(home) and time.monotonic()<deadline: time.sleep(.05)
        assert not owned_pids(home), 'test-owned session processes survived explicit reap'
        show=subprocess.run(['systemctl','--user','show',unit,'-p','ControlGroup','--value'],
                            capture_output=True,text=True,check=True,timeout=5)
        cg_rel=show.stdout.strip().lstrip('/')
        assert cg_rel and '..' not in pathlib.PurePosixPath(cg_rel).parts
        cg=pathlib.Path('/sys/fs/cgroup')/cg_rel/'cgroup.events'
        events=cg.read_text()
        assert 'populated 0' in events, (unit,cg,events)
        assert home.exists(), 'expected crash residue missing before recovery'
        restarted=start()
        assert not list(root.iterdir()), f'stale session root remains: {list(root.iterdir())}'
        dropin=pathlib.Path(os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'))/\
            'systemd/user.control'/f'{unit}.d'
        assert not dropin.exists(), f'runtime drop-in remains: {dropin}'
        restarted.terminate(); restarted.wait(timeout=8)
        assert restarted.returncode == 0, restarted.stderr.read().decode()
        restarted=None
        props=subprocess.run(['systemctl','--user','show',unit,'-p','ActiveState','-p','MemoryMax','-p','TasksMax'],
                             capture_output=True,text=True,timeout=5)
        values=dict(line.split('=',1) for line in props.stdout.splitlines() if '=' in line)
        assert props.returncode or (values.get('ActiveState')=='inactive' and
            values.get('MemoryMax')=='infinity' and values.get('TasksMax')=='infinity'), values
        print(json.dumps({'ok':True,'broker_killed':'SIGKILL','owned_pids_explicitly_reaped':pids,
                          'cgroup_empty_before_restart':True,'recovered_session_root_absent':True,
                          'runtime_dropin_absent':True,'unit_after_stop':values}))
    finally:
        for proc in (server,restarted):
            if proc is not None and proc.poll() is None:
                proc.kill(); proc.wait(timeout=3)
        if root.exists() and any(root.iterdir()):
            raise RuntimeError('refusing to discard private session residue without recovery: '+str(list(root.iterdir())))
        shutil.rmtree(td, ignore_errors=True)

if __name__ == '__main__': main()
