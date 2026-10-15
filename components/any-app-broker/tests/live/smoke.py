#!/usr/bin/env python3
"""End-to-end Stage 1 smoke test; destructive effects are confined to temp files."""
import base64, json, os, pathlib, socket, stat, subprocess, sys, tempfile, time
ROOT = pathlib.Path(__file__).resolve().parents[2]
BROKER = ROOT / 'any_app_broker' / 'broker.py'
AGGREGATE_LIMITS = '--aggregate-limits' in sys.argv
if AGGREGATE_LIMITS:
    sys.argv.remove('--aggregate-limits')

def request(sock, req):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(20); s.connect(str(sock)); s.sendall(json.dumps(req).encode()+b'\n')
        b = bytearray()
        while not b.endswith(b'\n'):
            x=s.recv(65536)
            if not x: break
            b.extend(x)
    result=json.loads(b)
    return result

def ok(resp):
    assert resp['ok'], resp
    return resp['result']

with tempfile.TemporaryDirectory(prefix='orbit-gui-test-') as td:
    tmp=pathlib.Path(td); sock=tmp/'broker.sock'; root=tmp/'sessions'; evidence=tmp/'evidence'
    broker_command=[sys.executable,str(BROKER),'--socket',str(sock),'--root',str(root),
                    '--evidence-root',str(evidence)] + ([ '--aggregate-limits'] if AGGREGATE_LIMITS else []) + ['serve']
    def start_broker():
        proc=subprocess.Popen(broker_command, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, start_new_session=True)
        for _ in range(100):
            if sock.exists(): return proc
            if proc.poll() is not None: raise RuntimeError(proc.stderr.read().decode())
            time.sleep(.1)
        proc.terminate(); proc.wait(timeout=5)
        raise RuntimeError('broker socket did not appear')
    def stop_broker(proc):
        proc.terminate()
        try: proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(timeout=3)
            raise RuntimeError('broker failed to stop gracefully')
        assert proc.returncode == 0, f'broker shutdown exit={proc.returncode}: {proc.stderr.read().decode()}'
        assert not sock.exists(), 'broker socket left behind'
    def session_processes(home):
        found=set()
        expected=b'HOME='+os.fsencode(home)
        for proc in pathlib.Path('/proc').iterdir():
            if not proc.name.isdigit(): continue
            try:
                if expected in (proc/'environ').read_bytes().split(b'\0'):
                    found.add(int(proc.name))
            except (OSError, PermissionError):
                continue
        return found
    def live_processes(pids):
        live=[]
        for pid in pids:
            try:
                tail=pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
                if tail[0] != 'Z': live.append(pid)
            except (OSError, IndexError):
                continue
        return live
    server=start_broker()
    try:
        for _ in range(100):
            if sock.exists(): break
            if server.poll() is not None: raise RuntimeError(server.stderr.read().decode())
            time.sleep(.1)
        assert sock.exists() and (sock.stat().st_mode & 0o777)==0o600
        one=ok(request(sock,{'op':'create'})); two=ok(request(sock,{'op':'create'}))
        session_units = []
        if AGGREGATE_LIMITS:
            def slice_properties(unit):
                result=subprocess.run(['systemctl','--user','show',unit,'-p','MemoryMax',
                                       '-p','TasksMax','-p','CPUQuotaPerSecUSec'],
                                      capture_output=True,text=True,check=True,timeout=5)
                return dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
            project_limits=slice_properties('orbit-any-app.slice')
            assert int(project_limits['MemoryMax']) == 3*1024**3, project_limits
            assert int(project_limits['TasksMax']) == 256, project_limits
            assert project_limits['CPUQuotaPerSecUSec'] in ('4s','4000000'), project_limits
            session_units=[f"orbit-any-app-{s['session_id']}.slice" for s in (one,two)]
            session_limits=[slice_properties(unit) for unit in session_units]
            for limits in session_limits:
                assert int(limits['MemoryMax']) == 1024**3, limits
                assert int(limits['TasksMax']) == 64, limits
                assert limits['CPUQuotaPerSecUSec'] in ('2s','2000000'), limits
            for session in (one,two):
                unit=f"orbit-any-app-{session['session_id']}.slice"
                control_group=subprocess.run(['systemctl','--user','show',unit,
                                              '-p','ControlGroup','--value'],
                                             capture_output=True,text=True,
                                             check=True,timeout=5).stdout.strip()
                assert control_group.startswith('/'), (unit, control_group)
                # systemd-cgls truncates long executable paths by default. Inspect
                # procfs directly and require each expected child to be inside the
                # exact slice subtree rather than trusting presentation output.
                observed=[]
                for proc in pathlib.Path('/proc').iterdir():
                    if not proc.name.isdigit(): continue
                    try:
                        cgroups=(proc/'cgroup').read_text()
                        cmdline=(proc/'cmdline').read_bytes().replace(b'\\0',b' ').decode(errors='replace')
                    except (OSError, PermissionError):
                        continue
                    cg_path=next((row.rsplit(':',1)[-1] for row in cgroups.splitlines()
                                  if row.startswith('0::')), '')
                    if cg_path.startswith(control_group + '/'):
                        observed.append((cg_path,cmdline))
                assert any('Xvfb' in cmd for _,cmd in observed), (unit, observed)
                assert any('form_app.py' in cmd for _,cmd in observed), (unit, observed)
        def req(s,op,**kw): return request(sock,dict(op=op,session_id=s['session_id'],capability=s['capability'],**kw))
        frame1=ok(req(one,'observe')); frame2=ok(req(two,'observe'))
        evidence_file=evidence/one['session_id']/'frame-0001.png'
        assert evidence_file.exists(), 'broker must persist the observed frame'
        persisted_png=evidence_file.read_bytes()
        assert persisted_png == base64.b64decode(frame1['frame_base64']), 'persisted frame differs from response'
        assert stat.S_IMODE(evidence.stat().st_mode) == 0o700, 'evidence root must be owner-only'
        assert stat.S_IMODE(evidence_file.parent.stat().st_mode) == 0o700, 'session evidence dir must be owner-only'
        assert stat.S_IMODE(evidence_file.stat().st_mode) == 0o600, 'evidence PNG must not be group/world accessible'
        assert frame1['width']==1280 and frame1['height']==800 and frame1['bytes']<3*1024*1024
        image=base64.b64decode(frame1['frame_base64'],validate=True)
        assert image.startswith(b'\x89PNG\r\n\x1a\n') and len(image)==frame1['bytes']
        wrong=request(sock,{'op':'observe','session_id':one['session_id'],'capability':'wrong'})
        assert not wrong['ok'] and wrong['error_type']=='PermissionError'
        foreign=req(one,'click',frame_id=frame2['frame_id'],x=10,y=80)
        assert not foreign['ok'] and 'stale or foreign' in foreign['error']
        frame1=ok(req(one,'observe'))
        stale=req(one,'click',frame_id=frame1['frame_id'],x=250,y=80)
        assert stale['ok']; frame=ok(req(one,'observe'))
        focused=req(one,'click',frame_id=frame['frame_id'],x=250,y=166)
        assert focused['ok']; frame=ok(req(one,'observe'))
        typed=req(one,'type',frame_id=frame['frame_id'],text='Stage One Verified')
        assert typed['ok']; frame=ok(req(one,'observe'))
        clicked=req(one,'click',frame_id=frame['frame_id'],x=100,y=220)
        assert clicked['ok']; time.sleep(.3)
        # An input changes the scene revision; reusing its pre-action screenshot
        # must be rejected until a fresh observation is fetched.
        stale_after_click=req(one,'click',frame_id=frame['frame_id'],x=100,y=220)
        assert (not stale_after_click['ok'] and
                'stale or foreign frame_id' in stale_after_click['error']), stale_after_click
        first_dir=next(p for p in root.iterdir() if p.name.startswith(one['session_id']))
        state_path=first_dir/'app-state.json'
        if not state_path.exists():
            dbg=subprocess.run(['xdotool','search','--onlyvisible','--name','Orbit Form Smoke'],capture_output=True,text=True)
            print('DEBUG app state missing; windows:',dbg.stdout, file=sys.stderr)
            raise AssertionError(f'Save click did not persist app state; session files: {list(first_dir.iterdir())}')
        saved=json.loads(state_path.read_text())
        assert saved=={'name':'Stage One Verified','saved':True}, saved
        # Exercise a real menu command and use the file-backed app state as oracle.
        frame=ok(req(one,'observe'))
        menu_open=req(one,'click',frame_id=frame['frame_id'],x=55,y=55)
        assert menu_open['ok'], menu_open
        frame=ok(req(one,'observe'))
        reset_click=req(one,'click',frame_id=frame['frame_id'],x=55,y=83)
        assert reset_click['ok'], reset_click
        time.sleep(.2)
        reset=json.loads(state_path.read_text())
        assert reset=={'name':'','saved':False}, reset
        # Exercise Help -> About, assert the dialog is a visible native window and
        # preserve screenshot evidence. Dismiss via Escape and verify it is gone.
        before_about=ok(req(one,'observe'))
        help_open=req(one,'click',frame_id=before_about['frame_id'],x=110,y=55)
        assert help_open['ok'], help_open
        menu_frame=ok(req(one,'observe'))
        assert (base64.b64decode(menu_frame['frame_base64']) !=
                base64.b64decode(before_about['frame_base64'])), \
            'Help menu interaction did not change the visible frame'
        about_click=req(one,'click',frame_id=menu_frame['frame_id'],x=120,y=83)
        assert about_click['ok'], about_click
        time.sleep(.2)
        dialog_windows=subprocess.run(['xdotool','search','--onlyvisible','--name',
                                       '^About Orbit Form Smoke$'],env={**os.environ,
                                       'DISPLAY':one['display'],
                                       'XAUTHORITY':str(first_dir/'Xauthority')},
                                      capture_output=True,text=True,check=False)
        assert (dialog_windows.returncode == 0 and
                dialog_windows.stdout.strip()), \
            f'About dialog not visible: {dialog_windows.stderr}'
        about_frame=ok(req(one,'observe'))
        about_png=base64.b64decode(about_frame['frame_base64'])
        assert (about_png != base64.b64decode(menu_frame['frame_base64'])), \
            'About dialog did not alter captured screen'
        (evidence/one['session_id']/'about-dialog.png').write_bytes(about_png)
        dismissed=req(one,'key',frame_id=about_frame['frame_id'],key='Escape')
        assert dismissed['ok'], dismissed
        time.sleep(.1)
        gone=subprocess.run(['xdotool','search','--onlyvisible','--name',
                             '^About Orbit Form Smoke$'],env={**os.environ,
                             'DISPLAY':one['display'],
                             'XAUTHORITY':str(first_dir/'Xauthority')},
                            capture_output=True,text=True,check=False)
        assert gone.returncode != 0, f'About dialog remained visible: {gone.stdout}'
        # A second session still has its own untouched form; the first session's input did not leak.
        second_dir=next(p for p in root.iterdir() if p.name.startswith(two['session_id']))
        assert not (second_dir/'app-state.json').exists()
        final=ok(req(one,'observe'))
        (tmp/'final.png').write_bytes(base64.b64decode(final['frame_base64']))
        session_paths=[next(p for p in root.iterdir() if p.name.startswith(s['session_id']))
                       for s in (one,two)]
        session_pids={str(path):session_processes(path) for path in session_paths}
        assert all(session_pids.values()), f'could not identify session-owned processes: {session_pids}'
        stop_broker(server)
        assert not any(root.iterdir()), f'orderly shutdown left session directories: {list(root.iterdir())}'
        remaining={home:live_processes(pids) for home,pids in session_pids.items()}
        assert not any(remaining.values()), f'broker left live session processes: {remaining}'
        # Restart with the same evidence store; session directories must remain empty
        # and durable evidence must pass startup reconciliation unchanged.
        original_evidence={p.relative_to(evidence): p.read_bytes()
                           for p in evidence.rglob('*.png')}
        server=start_broker()
        assert not any(root.iterdir()), 'restart resurrected session state'
        if AGGREGATE_LIMITS:
            runtime=pathlib.Path(os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'))
            control=runtime/'systemd'/'user.control'
            for unit in session_units:
                assert not (control/f'{unit}.d').exists(), \
                    f'restart retained a session runtime drop-in: {unit}'
                state=subprocess.run(['systemctl','--user','show',unit,'-p','LoadState',
                                      '-p','ActiveState','-p','MemoryMax','-p','TasksMax'],
                                     capture_output=True,text=True,timeout=5)
                props=dict(line.split('=',1) for line in state.stdout.splitlines() if '=' in line)
                assert state.returncode != 0 or (
                    props.get('ActiveState') == 'inactive' and
                    props.get('MemoryMax') == 'infinity' and
                    props.get('TasksMax') == 'infinity'), \
                    f'restart did not reconcile session slice {unit}: {props}'
        assert original_evidence == {p.relative_to(evidence): p.read_bytes()
                                     for p in evidence.rglob('*.png')}, \
            'restart changed valid retained evidence'
        stop_broker(server)
        print(json.dumps({'ok':True,'sessions':2,'frame_size':frame1['bytes'],'saved_state':saved,
                          'bad_capability':'denied','foreign_frame':'denied','independent_session':True,
                          'cleanup':'verified','restart':'verified','aggregate_limits':AGGREGATE_LIMITS,
                          'evidence_png':str(evidence/one['session_id'])}))
    finally:
        if server.poll() is None:
            try: stop_broker(server)
            except Exception:
                if server.poll() is None:
                    server.kill(); server.wait()
        # Ensure the session-root's X server/app process groups are gone (directories removed).
        if root.exists(): assert not any(root.iterdir()), list(root.iterdir())
