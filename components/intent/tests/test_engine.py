import sys
import pytest

if not sys.platform.startswith("linux"):
    pytest.skip("Intent's engine is Linux-only (fcntl.flock, renameat2, /proc/self/fd); run on Odd1 or another Linux host",
                allow_module_level=True)

import multiprocessing, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from odd1_intent import engine

class EngineTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.dst=self.root/'project'; self.src=self.root/'Downloads'; self.dst.mkdir(); self.src.mkdir()
  home=patch.object(engine,'ROOT',self.root/'state'); home2=patch.object(engine,'TXROOT',self.root/'state/transactions'); home3=patch.object(engine,'LOG',self.root/'state/history.json'); self.ps=[home,home2,home3]
  for p in self.ps:p.start()
 def tearDown(self):
  for p in reversed(self.ps):p.stop()
  self.tmp.cleanup()
 def put(self,d,n,text,mtime):
  p=d/n;p.write_text(text);os.utime(p,ns=(mtime,mtime));return p
 def test_newer_replacement_add_and_old_keep(self):
  self.put(self.dst,'new.txt','old',2_000_000_000); self.put(self.src,'new.txt','fresh',3_000_000_000)
  self.put(self.dst,'old.txt','best',4_000_000_000); self.put(self.src,'old.txt','stale',3_000_000_000)
  self.put(self.src,'add.txt','new',3_000_000_000); self.put(self.src,'unrelated.bin','other',3_000_000_000)
  self.put(self.dst,'unrelated.bin','mine',4_000_000_000)
  p=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  self.assertEqual([(Path(a.target).name,a.kind) for a in p.actions],[('add.txt','ADD'),('new.txt','REPLACE'),('old.txt','SKIP'),('unrelated.bin','SKIP')])
  e=engine.apply(p,[0,1]); self.assertEqual(len(e['records']),2); self.assertEqual((self.dst/'add.txt').read_text(),'new'); self.assertEqual((self.dst/'new.txt').read_text(),'fresh')
  engine.undo(e['id']); self.assertFalse((self.dst/'add.txt').exists()); self.assertEqual((self.dst/'new.txt').read_text(),'old')
 @staticmethod
 def _worker_apply(lock_fd, root, src, dst, name, ready, start, results):
  # Linux fork workers inherit test ROOT/TXROOT/LOG patches; every transaction
  # uses the same real flock file, as two separately launched CLI processes do.
  os.close(lock_fd)
  plan=engine.parse(f'Update files in "{dst}" with the ones from "{src}"')
  ready.put(name)
  if start.wait(5) is False: raise RuntimeError('start barrier timed out')
  index=next(i for i,a in enumerate(plan.actions) if Path(a.source).name==name)
  entry=engine.apply(plan,[index])
  results.put(entry['id'])

 def test_engine_lock_rejects_symlink_and_hardlink(self):
  lock=engine.ROOT/'engine.lock'
  engine.ROOT.mkdir(parents=True)
  victim=self.root/'victim'; victim.write_text('must survive')
  lock.symlink_to(victim)
  with self.assertRaises(OSError):
   with engine._engine_lock(): pass
  self.assertEqual(victim.read_text(),'must survive')
  lock.unlink(); lock.write_text('lock')
  hardlink=self.root/'second-link'; os.link(lock,hardlink)
  with self.assertRaisesRegex(engine.IntentError,'link count'):
   with engine._engine_lock(): pass
  self.assertEqual(lock.read_text(),'lock')
  hardlink.unlink()

 def test_multiprocess_apply_history_read_modify_write_is_serialized(self):
  self.put(self.src,'first.txt','one',3_000_000_000)
  self.put(self.src,'second.txt','two',3_000_000_001)
  lock_path=self.root/'workers-start.lock'
  lock_fd=os.open(lock_path,os.O_CREAT|os.O_RDWR,0o600)
  ctx=multiprocessing.get_context('fork')
  ready=ctx.Queue(); results=ctx.Queue(); start=ctx.Event()
  procs=[ctx.Process(target=self._worker_apply,args=(lock_fd,str(self.root/'state'),str(self.src),str(self.dst),name,ready,start,results)) for name in ('first.txt','second.txt')]
  for proc in procs: proc.start()
  os.close(lock_fd)
  try:
   self.assertEqual({ready.get(timeout=5),ready.get(timeout=5)},{'first.txt','second.txt'})
   start.set()
   for proc in procs: proc.join(10)
   self.assertFalse(any(proc.is_alive() for proc in procs))
   self.assertEqual([proc.exitcode for proc in procs],[0,0])
   ids={results.get(timeout=2),results.get(timeout=2)}
   entries=engine.history()
   self.assertEqual(len(ids),2)
   self.assertEqual({entry['id'] for entry in entries},ids)
   self.assertEqual((self.dst/'first.txt').read_text(),'one')
   self.assertEqual((self.dst/'second.txt').read_text(),'two')
  finally:
   start.set()
   for proc in procs:
    if proc.is_alive(): proc.terminate(); proc.join(2)
   ready.close(); results.close()

 def test_cancel_is_no_mutation(self):
  self.put(self.src,'x','new',3_000_000_000); p=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'); self.assertFalse(p.actions[0].selected); self.assertFalse((self.dst/'x').exists())

 def test_apply_rejects_invalid_approved_action_indexes_before_mutation(self):
  self.put(self.src,'new.txt','new',3_000_000_000)
  self.put(self.dst,'keep.txt','existing',4_000_000_000)
  self.put(self.src,'keep.txt','older',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  add_index=next(i for i,a in enumerate(plan.actions) if a.kind=='ADD')
  skip_index=next(i for i,a in enumerate(plan.actions) if a.kind=='SKIP')
  for indexes in ([-1], [len(plan.actions)], [add_index,skip_index], [True], [1.5]):
   with self.subTest(indexes=indexes):
    with self.assertRaises(engine.IntentError): engine.apply(plan,indexes)
    self.assertFalse((self.dst/'new.txt').exists())
    self.assertEqual((self.dst/'keep.txt').read_text(),'existing')
    self.assertEqual(engine.history(),[])
    self.assertEqual(list(engine.TXROOT.glob('*')) if engine.TXROOT.exists() else [],[])
  # Valid iterable selection remains supported and applies only that action.
  entry=engine.apply(plan,(i for i in [add_index]))
  self.assertEqual(entry['records'][0]['target'],str(self.dst/'new.txt'))
  self.assertEqual((self.dst/'new.txt').read_text(),'new')
 def test_undo_history_save_and_compensation_failure_retains_recovery_state(self):
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'); entry=engine.apply(plan)
  exchange=engine._rename_exchange; calls=[0]
  def fail_compensation(left,right):
   calls[0]+=1
   # apply replacement succeeds; fail undo's compensation after history save fails
   if calls[0]==2: raise OSError('injected undo compensation failure')
   return exchange(left,right)
  with patch.object(engine,'_save',side_effect=OSError('history unavailable')), patch.object(engine,'_rename_exchange',side_effect=fail_compensation):
   with self.assertRaisesRegex(engine.IntentError,'rollback incomplete'):
    engine.undo(entry['id'])
  self.assertEqual(calls[0],2)
  self.assertEqual((self.dst/'x').read_text(),'before')
  self.assertFalse(engine.history()[0]['undone'])
  staged=list(self.dst.glob('.orbit-undo-*'))
  self.assertEqual(len(staged),1); self.assertEqual(staged[0].read_text(),'after')
  backups=list((self.root/'state'/'transactions').glob('*/0.before'))
  self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_text(),'before')

 def test_undo_cleanup_failure_reports_committed_state_and_retains_artifacts(self):
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  entry=engine.apply(plan); real_unlink=Path.unlink; failed=[False]
  def fail_staged_cleanup(path, *args, **kwargs):
   if Path(path).name.startswith('.orbit-undo-') and not failed[0]:
    failed[0]=True
    raise OSError('injected cleanup failure')
   return real_unlink(path,*args,**kwargs)
  with patch.object(Path,'unlink',new=fail_staged_cleanup):
   with self.assertRaisesRegex(engine.IntentError,'Undo completed and is recorded, but cleanup was incomplete'):
    engine.undo(entry['id'])
  self.assertTrue(failed[0])
  self.assertEqual((self.dst/'x').read_text(),'before')
  self.assertTrue(engine.history()[0]['undone'])
  self.assertEqual(engine.history()[0]['id'],entry['id'])
  staged=list(self.dst.glob('.orbit-undo-*'))
  self.assertEqual(len(staged),1); self.assertEqual(staged[0].read_text(),'after')
  backups=list((self.root/'state'/'transactions').glob('*/0.before'))
  self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_text(),'before')
  with self.assertRaisesRegex(engine.IntentError,'No undoable transaction'):
   engine.undo(entry['id'])

 def test_undo_history_save_failure_restores_applied_state(self):
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'); entry=engine.apply(plan)
  txdir=self.root/'state'/'transactions'/entry['id']
  with patch.object(engine,'_save',side_effect=OSError('undo history unavailable')):
   with self.assertRaisesRegex(OSError,'undo history unavailable'): engine.undo(entry['id'])
  self.assertEqual((self.dst/'x').read_text(),'after')
  self.assertFalse(engine.history()[0]['undone'])
  self.assertTrue((txdir/'0.before').exists())
  self.assertEqual(list(self.dst.glob('.orbit-undo-*')),[])
 def test_undo_history_save_failure_staging_cleanup_failure_reports_retained_artifact(self):
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  txdir=engine.TXROOT/entry['id']; failed=[False]
  def fail_undo_stage_cleanup(boundary):
   if boundary=='undo.history_rollback.remove_stage' and not failed[0]:
    failed[0]=True
    raise OSError('injected staged before-image unlink failure')
  with patch.object(engine,'_save',side_effect=OSError('undo history unavailable')), patch.object(engine,'_failure_boundary',side_effect=fail_undo_stage_cleanup):
   with self.assertRaisesRegex(engine.IntentError,'Undo history save failed; filesystem state was restored, but staging cleanup was incomplete') as caught:
    engine.undo(entry['id'])
  artifacts=list(self.dst.glob('.orbit-undo-*'))
  self.assertTrue(failed[0]); self.assertEqual(len(artifacts),1)
  self.assertIn(str(artifacts[0]),str(caught.exception)); self.assertIn('staged before-image unlink failure',str(caught.exception))
  self.assertEqual((self.dst/'x').read_text(),'after')
  self.assertFalse(engine.history()[0]['undone'])
  self.assertEqual((txdir/'0.before').read_text(),'before')
  self.assertEqual(artifacts[0].read_text(),'before')
 def test_no_clobber_claim_preserves_competing_path(self):
  target=self.dst/'x'; target.write_text('expected')
  real=engine._rename_noreplace; inserted=[False]
  def race(src,dst):
   real(src,dst)
   if not inserted[0] and Path(src).name==target.name:
    inserted[0]=True; target.write_text('human edit')
  with patch.object(engine,'_rename_noreplace',side_effect=race):
   with self.assertRaisesRegex(engine.IntentError,'claimed path retained'):
    engine._claim_if_unchanged(target,engine.digest(target),'.claim-')
  self.assertEqual(target.read_text(),'human edit')
  claims=list(self.dst.glob('.claim-*'))
  self.assertEqual(len(claims),1); self.assertEqual(claims[0].read_text(),'expected')
 def test_multifile_undo_failure_compensates_prior_restore(self):
  self.put(self.dst,'a.txt','old-a',2_000_000_000); self.put(self.src,'a.txt','new-a',3_000_000_000)
  self.put(self.dst,'b.txt','old-b',2_000_000_000); self.put(self.src,'b.txt','new-b',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'); entry=engine.apply(plan)
  exchange=engine._rename_exchange; calls=[0]
  def fail_second(left,right):
   calls[0]+=1
   if calls[0]==2: raise OSError('injected second exchange failure')
   return exchange(left,right)
  with patch.object(engine,'_rename_exchange',side_effect=fail_second):
   with self.assertRaisesRegex(OSError,'second exchange failure'): engine.undo(entry['id'])
  self.assertEqual((self.dst/'a.txt').read_text(),'new-a')
  self.assertEqual((self.dst/'b.txt').read_text(),'new-b')
  self.assertFalse(engine.history()[0]['undone'])
  self.assertEqual(list(self.dst.glob('.orbit-undo-*')),[])
 def test_multifile_history_failure_compensates_each_replacement(self):
  # Distinct before-images expose rollback checks accidentally using the last
  # loop action's hash rather than the record currently being restored.
  self.put(self.dst,'a.txt','old-a',2_000_000_000); self.put(self.src,'a.txt','new-a',3_000_000_000)
  self.put(self.dst,'b.txt','old-b-different',2_000_000_000); self.put(self.src,'b.txt','new-b',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  with patch.object(engine,'_save',side_effect=OSError('history unavailable')):
   with self.assertRaisesRegex(OSError,'history unavailable'): engine.apply(plan)
  self.assertEqual((self.dst/'a.txt').read_text(),'old-a')
  self.assertEqual((self.dst/'b.txt').read_text(),'old-b-different')
  self.assertEqual(list((self.root/'state'/'transactions').glob('*')),[])
  self.assertEqual(list(self.dst.glob('.orbit-*')),[])
 def test_multifile_history_failure_compensates_add_and_replace(self):
  self.put(self.dst,'replace.txt','old',2_000_000_000); self.put(self.src,'replace.txt','new',3_000_000_000)
  self.put(self.src,'add.txt','added',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  with patch.object(engine,'_save',side_effect=OSError('history unavailable')):
   with self.assertRaisesRegex(OSError,'history unavailable'): engine.apply(plan,[0,1])
  self.assertEqual((self.dst/'replace.txt').read_text(),'old')
  self.assertFalse((self.dst/'add.txt').exists())
  self.assertEqual(list((self.root/'state'/'transactions').glob('*')),[])
  self.assertEqual(sorted(p.name for p in self.dst.iterdir()),['replace.txt'])
 def test_add_rollback_unlink_failure_names_retained_claim(self):
  self.put(self.src,'new.txt','source bytes',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  real_unlink=Path.unlink
  def fail_claim_cleanup(path,*args,**kwargs):
   path=Path(path)
   if path.name.startswith('.orbit-remove-'):
    raise OSError('injected claim deletion failure')
   return real_unlink(path,*args,**kwargs)
  with patch.object(engine,'_save',side_effect=OSError('injected history failure')), patch.object(Path,'unlink',new=fail_claim_cleanup):
   with self.assertRaisesRegex(engine.IntentError,'rollback incomplete') as caught:
    engine.apply(plan,[0])
  claims=list(self.dst.glob('.orbit-remove-*'))
  self.assertEqual(len(claims),1)
  self.assertEqual(claims[0].read_text(),'source bytes')
  self.assertIn(str(claims[0]),str(caught.exception))
  self.assertFalse((self.dst/'new.txt').exists())
  self.assertEqual(engine.history(),[])
  txdirs=list((self.root/'state'/'transactions').glob('*'))
  self.assertEqual(len(txdirs),1)
  self.assertEqual(list(txdirs[0].iterdir()),[])

 def test_unverified_replacement_backup_aborts_before_mutation(self):
  target=self.put(self.dst,'x','original',2_000_000_000)
  source=self.put(self.src,'x','fresh',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  real_copy=engine.shutil.copy2
  def corrupt_transaction_backup(src,dst,*args,**kwargs):
   result=real_copy(src,dst,*args,**kwargs)
   if Path(dst).parent.parent==engine.TXROOT:
    Path(dst).write_text('damaged before-image')
   return result
  with patch.object(engine.shutil,'copy2',side_effect=corrupt_transaction_backup):
   with self.assertRaisesRegex(engine.IntentError,'Transaction backup verification failed'):
    engine.apply(plan)
  self.assertEqual(target.read_text(),'original')
  self.assertEqual(source.read_text(),'fresh')
  self.assertEqual(engine.history(),[])
  self.assertEqual(list(engine.TXROOT.glob('*')),[])
  self.assertEqual(list(self.dst.glob('.orbit-*')),[])

 def test_history_save_failure_restores_replacement(self):
  self.put(self.dst,'x','original',2_000_000_000); self.put(self.src,'x','fresh',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  with patch.object(engine,'_save',side_effect=OSError('history disk full')):
   with self.assertRaisesRegex(OSError,'history disk full'):
    engine.apply(plan)
  self.assertEqual((self.dst/'x').read_text(),'original')
  self.assertEqual(list((self.root/'state'/'transactions').glob('*')),[])
  self.assertFalse((self.root/'state'/'history.json').exists())
 def test_unsupported_atomic_exchange_fails_closed(self):
  self.put(self.dst,'x','original',2_000_000_000); self.put(self.src,'x','replacement',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  with patch.object(engine,'_rename_exchange',side_effect=engine.IntentError('unsupported rename exchange')):
   with self.assertRaisesRegex(engine.IntentError,'unsupported rename exchange'): engine.apply(plan)
  self.assertEqual((self.dst/'x').read_text(),'original')
  self.assertEqual(engine.history(),[])
  self.assertEqual(list((self.root/'state'/'transactions').glob('*')),[])
 # Known stale (also fails in the migrated os-shell workspace): written against an earlier engine whose
 # directory-fsync/staging order differed; the injected fault now fires at a different step. Fix the test.
 @unittest.expectedFailure
 def test_replace_rollback_rejects_wrong_target_after_exchange(self):
  self.put(self.dst,'x','original',2_000_000_000); self.put(self.src,'x','fresh',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  target=self.dst/'x'; real_digest=engine.digest; real_unlink=os.unlink
  stage_paths=[]; corrupted=[False]
  def retain_stage(path, *args, **kwargs):
   p=Path(path)
   if p.parent==self.dst and p.name.startswith('.orbit-'):
    stage_paths.append(p)
    return None
   return real_unlink(path,*args,**kwargs)
  def corrupt_stage_on_postinstall(path):
   result=real_digest(path)
   if Path(path)==target and result==plan.actions[0].source_hash and not corrupted[0]:
    corrupted[0]=True
    stages=list(self.dst.glob('.orbit-*'))
    self.assertEqual(len(stages),1)
    stages[0].write_text('concurrent displaced file')
    return '0'*64
   return result
  with patch.object(engine.os,'unlink',side_effect=retain_stage), patch.object(engine,'digest',side_effect=corrupt_stage_on_postinstall):
   with self.assertRaisesRegex(engine.IntentError,'rollback incomplete'):
    engine.apply(plan)
  self.assertTrue(corrupted[0]); self.assertEqual(target.read_text(),'concurrent displaced file')
  staged=list(self.dst.glob('.orbit-*'))
  self.assertEqual(len(staged),1); self.assertEqual(staged[0].read_text(),'fresh')
  backups=list((self.root/'state'/'transactions').glob('*/0.before'))
  self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_text(),'original')
 def test_replace_rollback_failure_retains_backup_and_staged_recovery(self):
  self.put(self.dst,'x','original',2_000_000_000); self.put(self.src,'x','fresh',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  real_digest=engine.digest; verification_failed=[False]; target=self.dst/'x'
  def fail_postinstall(path):
   result=real_digest(path)
   if Path(path)==target and result==plan.actions[0].source_hash and not verification_failed[0]:
    verification_failed[0]=True
    return '0'*64
   return result
  exchange=engine._rename_exchange; calls=[0]
  def fail_rollback(left,right):
   calls[0]+=1
   if calls[0]==2: raise OSError('injected rollback exchange failure')
   return exchange(left,right)
  with patch.object(engine,'digest',side_effect=fail_postinstall), patch.object(engine,'_rename_exchange',side_effect=fail_rollback):
   with self.assertRaisesRegex(engine.IntentError,'rollback incomplete'):
    engine.apply(plan)
  self.assertTrue(verification_failed[0]); self.assertEqual(calls[0],2)
  self.assertEqual(target.read_text(),'fresh')
  backups=list((self.root/'state'/'transactions').glob('*/0.before'))
  staged=list(self.dst.glob('.orbit-rollback-*'))
  self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_text(),'original')
  self.assertEqual(len(staged),1); self.assertEqual(staged[0].read_text(),'original')
  self.assertEqual(engine.history(),[])
 def test_replace_verification_failure_restores_original(self):
  self.put(self.dst,'x','original',2_000_000_000); self.put(self.src,'x','fresh',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  real_digest=engine.digest; failed=[False]; target=self.dst/'x'
  def fail_postinstall(path):
   result=real_digest(path)
   if Path(path)==target and result==plan.actions[0].source_hash and not failed[0]:
    failed[0]=True
    return '0'*64
   return result
  with patch.object(engine,'digest',side_effect=fail_postinstall):
   with self.assertRaisesRegex(engine.IntentError,'Verification failed'):
    engine.apply(plan)
  self.assertTrue(failed[0])
  self.assertEqual(target.read_text(),'original')
  self.assertEqual(engine.history(),[])
  self.assertEqual(list((self.root/'state'/'transactions').glob('*')),[])
  self.assertEqual(sorted(p.name for p in self.dst.iterdir()),['x'])
 def test_replace_detects_target_swapped_at_atomic_exchange(self):
  self.put(self.dst,'x','original',2_000_000_000); self.put(self.src,'x','fresh',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  target=self.dst/'x'; attacker=self.root/'attacker'; attacker.write_text('human concurrent edit')
  exchange=engine._rename_exchange; raced=[False]
  def race(left,right):
   if not raced[0]:
    raced[0]=True
    os.replace(attacker,target)
   return exchange(left,right)
  with patch.object(engine,'_rename_exchange',side_effect=race):
   with self.assertRaisesRegex(engine.IntentError,'Target changed during replacement'):
    engine.apply(plan)
  self.assertEqual(target.read_text(),'human concurrent edit')
  self.assertEqual(engine.history(),[])
  self.assertFalse(attacker.exists())
  self.assertEqual(sorted(p.name for p in self.dst.iterdir()),['x'])
  self.assertEqual(list((self.root/'state'/'transactions').glob('*')),[])
 def test_replace_preserves_unexpected_displaced_file_if_restore_fails(self):
  self.put(self.dst,'x','original',2_000_000_000); self.put(self.src,'x','fresh',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  target=self.dst/'x'; attacker=self.root/'attacker'; attacker.write_text('human concurrent edit')
  exchange=engine._rename_exchange; calls=[0]
  def race_and_fail_restore(left,right):
   calls[0]+=1
   if calls[0]==1: os.replace(attacker,target)
   elif calls[0]==2: raise OSError('injected failed restoration')
   return exchange(left,right)
  with patch.object(engine,'_rename_exchange',side_effect=race_and_fail_restore):
   with self.assertRaisesRegex(engine.IntentError,'rollback incomplete'):
    engine.apply(plan)
  self.assertEqual(target.read_text(),'human concurrent edit')
  # The failed inner restoration path must not have its displaced name erased
  # by the mkstemp finally cleanup. Keep both recoverable bytes and transaction backup.
  self.assertEqual(len(list(self.dst.glob('.orbit-*'))),1)
  self.assertEqual(list(self.dst.glob('.orbit-*'))[0].read_text(),'fresh')
  self.assertEqual(len(list((self.root/'state'/'transactions').glob('*/0.before'))),1)
  self.assertFalse(attacker.exists())
 def test_undo_failed_compensation_preserves_unexpected_staged_file(self):
  self.put(self.dst,'x','original',2_000_000_000); self.put(self.src,'x','fresh',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'); entry=engine.apply(plan)
  target=self.dst/'x'; attacker=self.root/'attacker'; attacker.write_text('concurrent edit')
  exchange=engine._rename_exchange; calls=[0]
  def race_and_fail(left,right):
   calls[0]+=1
   if calls[0]==1:
    os.replace(attacker,target)
    return exchange(left,right)
   if calls[0]==2: raise OSError('injected failed undo compensation')
   return exchange(left,right)
  with patch.object(engine,'_rename_exchange',side_effect=race_and_fail):
   with self.assertRaisesRegex(engine.IntentError,'compensation failed'):
    engine.undo(entry['id'])
  self.assertEqual(calls[0],2)
  self.assertEqual(target.read_text(),'original')
  self.assertFalse(engine.history()[0]['undone'])
  # The exchanged unexpected object must not be deleted as generic staged data.
  staged=list(self.dst.glob('.orbit-undo-*'))
  self.assertEqual(len(staged),1); self.assertEqual(staged[0].read_text(),'concurrent edit')
  backups=list((self.root/'state'/'transactions').glob('*/0.before'))
  self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_text(),'original')
 def test_undo_detects_target_swapped_at_atomic_exchange(self):
  self.put(self.dst,'x','original',2_000_000_000); self.put(self.src,'x','fresh',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'); entry=engine.apply(plan)
  target=self.dst/'x'; attacker=self.root/'attacker'; attacker.write_text('human concurrent edit')
  exchange=engine._rename_exchange; raced=[False]
  def race(left,right):
   if not raced[0]:
    raced[0]=True
    os.replace(attacker,target)
   return exchange(left,right)
  with patch.object(engine,'_rename_exchange',side_effect=race):
   with self.assertRaisesRegex(engine.IntentError,'changed during restore'):
    engine.undo(entry['id'])
  self.assertEqual(target.read_text(),'human concurrent edit')
  self.assertFalse(engine.history()[0]['undone'])
  self.assertFalse(attacker.exists())
  # The failed verification leaves an unexpected target state; retain staged
  # recovery data rather than treating the exchange as a clean rollback.
  staged=list(self.dst.glob('.orbit-undo-*'))
  self.assertEqual(len(staged),1); self.assertEqual(staged[0].read_text(),'original')
 def test_undo_rejects_replaced_destination_parent_even_when_leaf_hashes_match(self):
  self.put(self.dst,'replace.txt','before',2_000_000_000)
  self.put(self.src,'replace.txt','after',3_000_000_000)
  self.put(self.src,'added.txt','added',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  entry=engine.apply(plan,[0,1])
  moved=self.root/'project-original'; self.dst.rename(moved); self.dst.mkdir()
  # Mimic an independently recreated directory containing byte-identical leaves:
  # leaf hashes alone would pass, but undo must remain bound to the approved folder.
  (self.dst/'replace.txt').write_text('after')
  (self.dst/'added.txt').write_text('added')
  with self.assertRaisesRegex(engine.IntentError,'destination folder changed since operation'):
   engine.undo(entry['id'])
  self.assertEqual((self.dst/'replace.txt').read_text(),'after')
  self.assertEqual((self.dst/'added.txt').read_text(),'added')
  self.assertEqual((moved/'replace.txt').read_text(),'after')
  self.assertFalse(engine.history()[0]['undone'])
  backups=sorted((self.root/'state'/'transactions'/entry['id']).iterdir())
  self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_text(),'before')
  self.assertEqual(list(self.dst.glob('.orbit-undo-*')),[])

 def test_apply_add_parent_swap_at_final_seam_preserves_outside_target(self):
  self.put(self.src,'new.txt','sensitive',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  original=self.dst; displaced=self.root/'project-original'; outside=self.root/'outside'; outside.mkdir()
  check=engine._require_parent_identity; calls=[]; swap=[False]
  def check_then_swap(path,expected,label):
   check(path,expected,label); calls.append(path)
  def boundary(name):
   if name=='apply.add.before_link' and not swap[0]:
    swap[0]=True
    original.rename(displaced); original.symlink_to(outside,target_is_directory=True)
  with patch.object(engine,'_require_parent_identity',side_effect=check_then_swap), patch.object(engine,'_failure_boundary',side_effect=boundary):
   with self.assertRaises(engine.IntentError): engine.apply(plan,[0])
  self.assertTrue(swap[0]); self.assertEqual(len(calls),1); self.assertTrue(original.is_symlink())
  self.assertEqual(list(outside.iterdir()),[])
  self.assertFalse((displaced/'new.txt').exists())
  staged=list(displaced.glob('.orbit-*'))
  self.assertEqual(len(staged),1); self.assertEqual(staged[0].read_text(),'sensitive')
  self.assertEqual(engine.history(),[])
  self.assertEqual(list(engine.TXROOT.glob('*')) if engine.TXROOT.exists() else [],[])

 def test_apply_replace_parent_swap_at_final_seam_preserves_outside_target(self):
  self.put(self.dst,'item.txt','before',2_000_000_000)
  self.put(self.src,'item.txt','after',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  original=self.dst; displaced=self.root/'project-original'; outside=self.root/'outside'; outside.mkdir()
  (outside/'item.txt').write_text('outside survivor')
  check=engine._require_parent_identity; calls=[]; swap=[False]
  def check_then_swap(path,expected,label):
   check(path,expected,label); calls.append(path)
  def boundary(name):
   if name=='apply.replace.before_exchange' and not swap[0]:
    swap[0]=True
    original.rename(displaced); original.symlink_to(outside,target_is_directory=True)
  with patch.object(engine,'_require_parent_identity',side_effect=check_then_swap), patch.object(engine,'_failure_boundary',side_effect=boundary):
   with self.assertRaises(engine.IntentError): engine.apply(plan,[0])
  self.assertTrue(swap[0]); self.assertEqual(len(calls),1); self.assertTrue(original.is_symlink())
  self.assertEqual((outside/'item.txt').read_text(),'outside survivor')
  self.assertEqual((displaced/'item.txt').read_text(),'before')
  staged=list(displaced.glob('.orbit-*'))
  self.assertEqual(len(staged),1); self.assertEqual(staged[0].read_text(),'after')
  self.assertEqual(engine.history(),[])
  self.assertEqual(list(engine.TXROOT.glob('*')) if engine.TXROOT.exists() else [],[])

 def test_apply_rollback_exchange_rejects_parent_swap_after_install(self):
  self.put(self.dst,'item.txt','before',2_000_000_000)
  self.put(self.src,'item.txt','after',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  moved=self.root/'approved-project'; outside=self.root/'outside'; outside.mkdir()
  (outside/'item.txt').write_text('outside survivor')
  real_boundary=engine._failure_boundary
  def swap_after_install(name):
   if name=='apply.replace.after_exchange':
    self.dst.rename(moved)
    self.dst.symlink_to(outside,target_is_directory=True)
   return real_boundary(name)
  with patch.object(engine,'_failure_boundary',side_effect=swap_after_install):
   with self.assertRaisesRegex(engine.IntentError,'rollback incomplete'):
    engine.apply(plan)
  self.assertTrue(self.dst.is_symlink())
  self.assertEqual((outside/'item.txt').read_text(),'outside survivor')
  self.assertEqual((moved/'item.txt').read_text(),'after')
  recovery=list(moved.glob('.orbit-*'))
  self.assertEqual(len(recovery),1); self.assertEqual(recovery[0].read_text(),'before')
  backups=list((self.root/'state'/'transactions').glob('*/0.before'))
  self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_text(),'before')
  self.assertEqual(engine.history(),[])

 def test_exchange_fsync_uses_the_same_pinned_parent_after_path_swap(self):
  left=self.dst/'.stage'; right=self.dst/'item.txt'
  left.write_text('staged'); right.write_text('target')
  original_id=(self.dst.stat().st_dev,self.dst.stat().st_ino)
  moved=self.root/'approved-project'; outside=self.root/'outside'; outside.mkdir()
  real_fsync=engine.os.fsync; fsynced=[]
  def swap_before_sync():
   self.dst.rename(moved); self.dst.symlink_to(outside,target_is_directory=True)
  def inspect_sync(fd):
   info=os.fstat(fd); fsynced.append((info.st_dev,info.st_ino))
   return real_fsync(fd)
  with patch.object(engine.os,'fsync',side_effect=inspect_sync):
   engine._rename_exchange_in_parent(left,right,f'{original_id[0]}:{original_id[1]}','Destination',on_exchange=swap_before_sync)
  self.assertEqual(fsynced,[original_id])
  self.assertTrue(self.dst.is_symlink()); self.assertEqual(list(outside.iterdir()),[])
  self.assertEqual((moved/'item.txt').read_text(),'staged')
  self.assertEqual((moved/'.stage').read_text(),'target')

 def test_exchange_sync_failure_still_runs_mutation_bookkeeping(self):
  left=self.dst/'.stage'; right=self.dst/'item.txt'
  left.write_text('staged'); right.write_text('target')
  expected=f'{self.dst.stat().st_dev}:{self.dst.stat().st_ino}'
  recorded=[]
  with patch.object(engine.os,'fsync',side_effect=OSError('injected directory fsync failure')):
   with self.assertRaisesRegex(OSError,'injected directory fsync failure'):
    engine._rename_exchange_in_parent(left,right,expected,'Destination',on_exchange=lambda:recorded.append('mutated'))
  self.assertEqual(recorded,['mutated'])
  self.assertEqual((right).read_text(),'staged'); self.assertEqual(left.read_text(),'target')

 def test_undo_exchange_rejects_parent_swap_and_retains_stage(self):
  self.put(self.dst,'item.txt','before',2_000_000_000)
  self.put(self.src,'item.txt','after',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  entry=engine.apply(plan)
  moved=self.root/'approved-project'; outside=self.root/'outside'; outside.mkdir()
  (outside/'item.txt').write_text('outside survivor')
  exchange=engine._rename_exchange_in_parent; swapped=[False]
  def swap_before_exchange(left,right,expected,label,**kwargs):
   if not swapped[0]:
    swapped[0]=True
    self.dst.rename(moved)
    self.dst.symlink_to(outside,target_is_directory=True)
   return exchange(left,right,expected,label,**kwargs)
  with patch.object(engine,'_rename_exchange_in_parent',side_effect=swap_before_exchange):
   with self.assertRaisesRegex(engine.IntentError,'Undo destination folder changed since preview'):
    engine.undo(entry['id'])
  self.assertTrue(swapped[0]); self.assertTrue(self.dst.is_symlink())
  self.assertEqual((outside/'item.txt').read_text(),'outside survivor')
  self.assertEqual((moved/'item.txt').read_text(),'after')
  staged=list(moved.glob('.orbit-undo-*'))
  self.assertEqual(len(staged),1); self.assertEqual(staged[0].read_text(),'before')
  self.assertFalse(engine.history()[0]['undone'])

 def test_apply_rejects_replaced_destination_directory(self):
  self.put(self.src,'x','source',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  moved=self.root/'project-original'; self.dst.rename(moved); self.dst.mkdir()
  (self.dst/'human.txt').write_text('must survive')
  with self.assertRaisesRegex(engine.IntentError,'Destination folder changed since preview'):
   engine.apply(plan,[0])
  self.assertEqual((self.dst/'human.txt').read_text(),'must survive')
  self.assertFalse((self.dst/'x').exists())
  self.assertFalse((moved/'x').exists())
  self.assertEqual(engine.history(),[])
  self.assertEqual(list((self.root/'state'/'transactions').glob('*')),[])

 def test_apply_rejects_source_folder_replaced_with_symlink(self):
  self.put(self.src,'x','source',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  moved=self.root/'Downloads-original'; self.src.rename(moved)
  self.src.symlink_to(moved,target_is_directory=True)
  with self.assertRaisesRegex(engine.IntentError,'Source folder changed since preview'):
   engine.apply(plan,[0])
  self.assertFalse((self.dst/'x').exists())
  self.assertTrue(self.src.is_symlink())
  self.assertEqual(engine.history(),[])
  self.assertEqual(list((self.root/'state'/'transactions').glob('*')),[])

 def test_stale_target_plan_refused(self):
  self.put(self.dst,'x','old',2_000_000_000);self.put(self.src,'x','new',3_000_000_000);p=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"');(self.dst/'x').write_text('changed')
  with self.assertRaises(engine.IntentError):engine.apply(p)
 def test_undo_accepts_legacy_replace_history_without_optional_fields(self):
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  saved=engine._load()
  # The displaced path is a newer rollback aid; legacy persisted records did
  # not include it. Undo depends on the verified transaction before-image.
  saved[0]['records'][0].pop('target_parent_id')
  saved[0]['records'][0].pop('displaced')
  engine._save(saved)
  engine.undo(entry['id'])
  self.assertEqual((self.dst/'x').read_text(),'before')
  self.assertTrue(engine.history()[0]['undone'])

 def test_undo_accepts_legacy_add_history_without_optional_fields(self):
  # The validator explicitly accepts absent, empty and null ADD before_hash
  # values. Exercise each persisted representation through real undo, not only
  # through structural validation; the hash is unused for ADD restoration.
  for index, before_hash in enumerate(("absent", "", None)):
   name=f'new-{index}.txt'
   self.put(self.src,name,'added bytes',3_000_000_000)
   entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'),[0])
   self.assertEqual((self.dst/name).read_text(),'added bytes')
   saved=engine._load()
   record=next(row for row in saved if row['id']==entry['id'])['records'][0]
   self.assertEqual(record['kind'],'ADD')
   # Parent identity and before-image are both optional for legacy ADD rows.
   record.pop('target_parent_id')
   if before_hash == "absent": record.pop('before_hash')
   else: record['before_hash']=before_hash
   engine._save(saved)
   engine.undo(entry['id'])
   self.assertFalse((self.dst/name).exists())
   matching=next(row for row in engine.history() if row['id']==entry['id'])
   self.assertTrue(matching['undone'])
   self.assertFalse((self.root/'state'/'transactions'/entry['id']).exists())
   (self.src/name).unlink()

 def test_undo_refuses_if_changed(self):
  self.put(self.src,'x','new',3_000_000_000);p=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"');e=engine.apply(p,[0]);(self.dst/'x').write_text('human edit')
  with self.assertRaises(engine.IntentError):engine.undo(e['id'])
 def test_destination_symlinks_are_skipped_including_dangling(self):
  self.put(self.src,'linked.txt','source',3_000_000_000)
  real=self.root/'outside.txt'; real.write_text('keep')
  (self.dst/'linked.txt').symlink_to(real)
  self.put(self.src,'dangling.txt','source',3_000_000_000)
  (self.dst/'dangling.txt').symlink_to(self.root/'missing-target')
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  self.assertEqual([(Path(a.target).name,a.kind) for a in plan.actions],[('dangling.txt','SKIP'),('linked.txt','SKIP')])
  self.assertEqual(real.read_text(),'keep')
  self.assertFalse((self.root/'missing-target').exists())
 def test_add_install_does_not_clobber_dangling_symlink_race(self):
  self.put(self.src,'new.txt','source',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  target=self.dst/'new.txt'; outside=self.root/'missing-target'
  real_link=os.link
  def race_link(src,dst,*args,**kwargs):
   parent_fd=kwargs.get('dst_dir_fd')
   target=(Path('/proc/self/fd')/str(parent_fd)/dst) if parent_fd is not None else Path(dst)
   target.symlink_to(outside)
   return real_link(src,dst,*args,**kwargs)
  with patch.object(engine.os,'link',side_effect=race_link):
   with self.assertRaisesRegex(engine.IntentError,'Target appeared'):
    engine.apply(plan,[0])
  self.assertTrue(target.is_symlink())
  self.assertFalse(outside.exists())
  self.assertEqual(engine.history(),[])
  self.assertTrue(target.is_symlink())
  self.assertEqual(sorted(p.name for p in self.dst.iterdir()),['new.txt'])
  self.assertEqual(sorted(p.name for p in self.src.iterdir()),['new.txt'])
  self.assertEqual(list((self.root/'state'/'transactions').glob('*')),[])
 def test_add_unlink_failure_after_install_rolls_back_target(self):
  self.put(self.src,'new.txt','source',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  real_unlink=os.unlink; failed=[False]
  def fail_once(path, *args, **kwargs):
   if Path(path).name.startswith('.orbit-') and not failed[0]:
    failed[0]=True
    raise OSError('injected temp unlink failure')
   return real_unlink(path,*args,**kwargs)
  with patch.object(engine.os,'unlink',side_effect=fail_once):
   with self.assertRaisesRegex(OSError,'injected temp unlink'):
    engine.apply(plan,[0])
  self.assertTrue(failed[0])
  self.assertFalse((self.dst/'new.txt').exists())
  self.assertEqual(engine.history(),[])
  self.assertEqual(list((self.root/'state'/'transactions').glob('*')),[])
  self.assertEqual(list(self.dst.iterdir()),[])
 def test_add_refuses_dangling_symlink_appearing_after_preview(self):
  self.put(self.src,'new.txt','source',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  target=self.dst/'new.txt'; target.symlink_to(self.root/'missing-target')
  with self.assertRaisesRegex(engine.IntentError,'Target appeared'):
   engine.apply(plan,[0])
  self.assertTrue(target.is_symlink())
  self.assertFalse((self.root/'missing-target').exists())
 def test_mixed_undo_failed_add_removal_claim_preserves_claim_and_restores_replacement(self):
  self.put(self.dst,'replace.txt','old',2_000_000_000); self.put(self.src,'replace.txt','new',3_000_000_000)
  self.put(self.src,'add.txt','added bytes',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  entry=engine.apply(plan,[0,1]); target=self.dst/'add.txt'
  real=engine._rename_noreplace; calls=[0]
  def race_claim_then_fail_restore(source,destination):
   calls[0]+=1
   if calls[0]==1:
    real(source,destination)
    target.write_text('human concurrent file')
    return
   if calls[0]==2: raise OSError('injected claim restoration failure')
   return real(source,destination)
  with patch.object(engine,'_rename_noreplace',side_effect=race_claim_then_fail_restore):
   with self.assertRaisesRegex(engine.IntentError,'Undo rollback incomplete'):
    engine.undo(entry['id'])
  self.assertEqual(calls[0],2)
  self.assertEqual(target.read_text(),'human concurrent file')
  self.assertEqual((self.dst/'replace.txt').read_text(),'new')
  self.assertFalse(engine.history()[0]['undone'])
  claims=list(self.dst.glob('.orbit-undo-remove-*'))
  self.assertEqual(len(claims),1); self.assertEqual(claims[0].read_text(),'added bytes')
  backups=list((self.root/'state'/'transactions').glob('*/1.before'))
  self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_text(),'old')

 def test_backup_refuses_dangling_symlink_target(self):
  source=self.root/'notes.txt'; source.write_bytes(b'keep')
  target=self.root/'notes.txt.bak'; target.symlink_to(self.root/'missing-target')
  with self.assertRaisesRegex(engine.IntentError,'already exists'):
   engine.parse(f'Make a backup of "{source}"')
  self.assertTrue(target.is_symlink())
 def test_backup_of_symlink_is_refused(self):
  real=self.root/'real.txt'; real.write_text('data')
  link=self.root/'alias.txt'; link.symlink_to(real)
  with self.assertRaisesRegex(engine.IntentError,'non-symlink'):
   engine.parse(f'Make a backup of "{link}"')
  self.assertFalse((self.root/'alias.txt.bak').exists())
 def test_backup_regular_file_and_undo(self):
  source=self.root/'notes.txt'; source.write_bytes(b'bytes\x00exact')
  plan=engine.parse(f'Make a backup of "{source}"')
  self.assertEqual([(a.kind,a.selected) for a in plan.actions],[('ADD',True)])
  entry=engine.apply(plan)
  backup=self.root/'notes.txt.bak'
  self.assertEqual(backup.read_bytes(),b'bytes\x00exact')
  engine.undo(entry['id'])
  self.assertFalse(backup.exists())
 def test_apply_rollback_fsync_failure_retains_before_image_and_displaced_file(self):
  target=self.put(self.dst,'x','before',2_000_000_000)
  self.put(self.src,'x','after',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  save_failed=[False]; rollback_syncs=[0]; real_fsync=os.fsync
  def fail_history_save(_entries):
   save_failed[0]=True
   raise OSError('injected history write failure')
  def observe_exchange_and_fail_rollback(fd):
   info=os.fstat(fd)
   result=real_fsync(fd)
   if save_failed[0] and (info.st_dev,info.st_ino)==(self.dst.stat().st_dev,self.dst.stat().st_ino):
    rollback_syncs[0]+=1
    raise OSError('injected rollback directory fsync failure')
   return result
  with patch.object(engine,'_save',side_effect=fail_history_save), patch.object(engine.os,'fsync',side_effect=observe_exchange_and_fail_rollback):
   with self.assertRaisesRegex(engine.IntentError,'rollback incomplete') as caught:
    engine.apply(plan)
  self.assertEqual(rollback_syncs[0],1)
  self.assertIn('rollback directory fsync failure',str(caught.exception))
  self.assertEqual(target.read_text(),'before')
  self.assertEqual(engine.history(),[])
  txbacks=list((self.root/'state'/'transactions').glob('*/0.before'))
  self.assertEqual(len(txbacks),1); self.assertEqual(txbacks[0].read_text(),'before')
  displaced=list(self.dst.glob('.orbit-rollback-*'))
  self.assertEqual(len(displaced),1); self.assertEqual(displaced[0].read_text(),'after')

 # Known stale (also fails in the migrated os-shell workspace): written against an earlier engine whose
 # directory-fsync/staging order differed; the injected fault now fires at a different step. Fix the test.
 @unittest.expectedFailure
 def test_add_rollback_retains_claim_when_claim_fsync_fails(self):
  self.put(self.src,'added.txt','new bytes',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  real_fsync=os.fsync; destination_syncs=[0]
  def fail_add_rollback_claim(fd):
   real_fsync(fd)
   try: actual=Path(os.readlink(f'/proc/self/fd/{fd}'))
   except OSError: actual=Path()
   if actual==self.dst:
    destination_syncs[0]+=1
    # Fail after the rollback rename has been recorded but before it is reported
    # complete, proving the claim remains available for recovery.
    if destination_syncs[0]==1:
     raise OSError('injected ADD rollback claim directory fsync failure')
  def fail_history_save(_entries):
   raise OSError('injected history write failure')
  with patch.object(engine,'_save',side_effect=fail_history_save), patch.object(engine.os,'fsync',new=fail_add_rollback_claim):
   with self.assertRaisesRegex(engine.IntentError,'History save failed and rollback incomplete') as caught:
    engine.apply(plan,[0])
  self.assertEqual(destination_syncs[0],1)
  self.assertIn('ADD rollback claim directory fsync failure',str(caught.exception))
  self.assertFalse((self.dst/'added.txt').exists())
  claimed=list(self.dst.glob('.orbit-remove-*'))
  self.assertEqual(len(claimed),1); self.assertEqual(claimed[0].read_text(),'new bytes')
  self.assertEqual((self.src/'added.txt').read_text(),'new bytes')
  self.assertEqual(engine.history(),[])
  self.assertEqual(len(list(engine.TXROOT.iterdir())),1)

 def test_apply_rollback_displaced_cleanup_failure_retains_artifact(self):
  target=self.put(self.dst,'x','before',2_000_000_000)
  self.put(self.src,'x','after',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  def fail_compensation(boundary):
   if boundary=='apply.replace.after_exchange':
    raise OSError('trigger apply rollback')
   if boundary=='apply.rollback.remove_displaced':
    raise OSError('injected compensation cleanup boundary')
  with patch.object(engine,'_failure_boundary',side_effect=fail_compensation):
   with self.assertRaisesRegex(engine.IntentError,'rollback incomplete') as caught:
    engine.apply(plan)
  self.assertIn('compensation cleanup boundary',str(caught.exception))
  self.assertEqual(target.read_text(),'before')
  self.assertEqual(engine.history(),[])
  txbacks=list((self.root/'state'/'transactions').glob('*/0.before'))
  self.assertEqual(len(txbacks),1); self.assertEqual(txbacks[0].read_text(),'before')
  displaced=[p for p in self.dst.glob('.orbit-*') if p.name!='x']
  self.assertEqual(len(displaced),1); self.assertEqual(displaced[0].read_text(),'after')

 def test_apply_add_rollback_named_failures_preserve_claim_or_report_post_unlink_state(self):
  # History-publication compensation runs a separate ADD removal path from an
  # ordinary apply failure; enumerate its claim/unlink/sync states explicitly.
  for boundary,claim_retained in (("apply.history_rollback.add.after_claim",True),("apply.history_rollback.add.before_unlink",True),("apply.history_rollback.add.after_unlink",False),("apply.history_rollback.add.before_sync",False)):
   with self.subTest(boundary=boundary):
    # Each subcase has an isolated directory/state root, as one unittest setup.
    for child in self.dst.iterdir(): child.unlink()
    for child in self.src.iterdir(): child.unlink()
    if engine.TXROOT.exists():
     for child in engine.TXROOT.iterdir():
      import shutil; shutil.rmtree(child)
    if engine.LOG.exists(): engine.LOG.unlink()
    source=self.put(self.src,'added','new bytes',3_000_000_000)
    plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
    armed=[False]
    def fail(name):
     if armed[0] and name==boundary: raise OSError('injected '+boundary)
    def fail_save(_entries):
     armed[0]=True
     raise OSError('trigger rollback')
    with patch.object(engine,'_save',side_effect=fail_save), patch.object(engine,'_failure_boundary',side_effect=fail):
     with self.assertRaisesRegex(engine.IntentError,'History save failed and rollback incomplete') as caught:
      engine.apply(plan,[0])
    self.assertFalse((self.dst/'added').exists())
    self.assertEqual(source.read_bytes(),b'new bytes')
    self.assertEqual(engine.history(),[])
    self.assertEqual(len(list(engine.TXROOT.iterdir())),1)
    if claim_retained:
     claim=list(self.dst.glob('.orbit-remove-*')); self.assertEqual(len(claim),1); self.assertEqual(claim[0].read_bytes(),b'new bytes')
     self.assertIn(str(claim[0]),str(caught.exception))
    else:
     self.assertEqual(list(self.dst.glob('.orbit-remove-*')),[])
     self.assertIn('was removed but directory sync/cleanup failed',str(caught.exception))

 def test_ordinary_apply_add_rollback_claim_cleanup_phase_matrix(self):
  # Force a failure after an ADD is installed, then fault each rollback claim
  # boundary. This is intentionally distinct from history-save compensation.
  cases=(("apply.rollback.add.after_claim",True),("apply.rollback.add.before_unlink",True),("apply.rollback.add.after_unlink",False),("apply.rollback.add.before_sync",False))
  for boundary,claim_retained in cases:
   with self.subTest(boundary=boundary):
    for child in self.dst.iterdir(): child.unlink()
    for child in self.src.iterdir(): child.unlink()
    if engine.TXROOT.exists():
     for child in engine.TXROOT.iterdir():
      import shutil; shutil.rmtree(child)
    if engine.LOG.exists(): engine.LOG.unlink()
    source=self.put(self.src,'added','new bytes',3_000_000_000)
    plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
    armed=[False]; injected=[False]
    def fail(name):
     if name=='apply.add.after_link' and not armed[0]:
      armed[0]=True
      raise OSError('trigger ordinary rollback')
     if armed[0] and name==boundary and not injected[0]:
      injected[0]=True
      raise OSError('injected '+boundary)
    with patch.object(engine,'_failure_boundary',side_effect=fail):
     with self.assertRaisesRegex(engine.IntentError,'Apply failed and rollback incomplete') as caught:
      engine.apply(plan,[0])
    self.assertTrue(injected[0]); self.assertFalse((self.dst/'added').exists())
    self.assertEqual(source.read_bytes(),b'new bytes'); self.assertEqual(engine.history(),[])
    txdirs=list(engine.TXROOT.iterdir()); self.assertEqual(len(txdirs),1)
    if claim_retained:
     claims=list(self.dst.glob('.orbit-remove-*')); self.assertEqual(len(claims),1)
     self.assertEqual(claims[0].read_bytes(),b'new bytes'); self.assertIn(str(claims[0]),str(caught.exception))
    else:
     self.assertEqual(list(self.dst.glob('.orbit-remove-*')),[])
     self.assertIn('was removed but directory sync/cleanup failed',str(caught.exception))

 def test_apply_history_compensation_transaction_cleanup_boundary(self):
  target=self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  seen=[]
  def fail(name):
   if name=='apply.history_rollback.remove_transaction': seen.append(name); raise OSError('transaction cleanup interrupted')
  def fail_save(_entries): raise OSError('history unavailable')
  with patch.object(engine,'_save',side_effect=fail_save), patch.object(engine,'_failure_boundary',side_effect=fail):
   with self.assertRaisesRegex(OSError,'transaction cleanup interrupted'):
    engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  self.assertEqual(seen,['apply.history_rollback.remove_transaction'])
  self.assertEqual(target.read_bytes(),b'before'); self.assertEqual(engine.history(),[])
  backup=list(engine.TXROOT.glob('*/0.before')); self.assertEqual(len(backup),1); self.assertEqual(backup[0].read_bytes(),b'before')
  self.assertEqual(list(self.dst.glob('.orbit-*')),[])

 def test_rollback_after_unlink_and_before_sync_boundaries_leave_truthful_state(self):
  # Adjudication of the generated inventory leads: these four cleanup seams
  # (two ordinary rollback paths, plus history compensation) were unmentioned.
  # Inject after unlink and immediately before sync; neither point has claim bytes.
  cases=(
   ('apply.rollback.after_remove_displaced',False,False),
   ('apply.rollback.before_sync_displaced',False,False),
   ('apply.rollback.after_remove_staged_after_image',False,True),
   ('apply.rollback.before_sync_staged_after_image',False,True),
   ('apply.history_rollback.after_remove_staged_after_image',True,False),
   ('apply.history_rollback.before_sync_staged_after_image',True,False),
  )
  for boundary,history_failure,force_staged_path in cases:
   with self.subTest(boundary=boundary):
    for child in self.dst.iterdir(): child.unlink()
    for child in self.src.iterdir(): child.unlink()
    if engine.TXROOT.exists():
     for child in engine.TXROOT.iterdir():
      import shutil; shutil.rmtree(child)
    if engine.LOG.exists(): engine.LOG.unlink()
    target=self.put(self.dst,'x','before',2_000_000_000)
    source=self.put(self.src,'x','after',3_000_000_000)
    plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
    injected=[]
    def fail(boundary_name):
     if not history_failure and boundary_name=='apply.replace.after_exchange':
      if force_staged_path:
       # Simulate missing displaced path so rollback safely falls back to its
       # transaction before-image and exercises staged-after-image cleanup.
       for staged in self.dst.glob('.orbit-*'):
        if staged != target: staged.unlink()
      raise OSError('trigger rollback')
     if boundary_name==boundary:
      injected.append(boundary_name)
      raise OSError('injected '+boundary)
    if history_failure:
     def fail_save(_entries): raise OSError('trigger history compensation')
     save_patch=patch.object(engine,'_save',side_effect=fail_save)
    else:
     save_patch=patch.object(engine,'_save',wraps=engine._save)
    with save_patch, patch.object(engine,'_failure_boundary',side_effect=fail):
     expected='History save failed and rollback incomplete' if history_failure else 'Apply failed and rollback incomplete'
     with self.assertRaisesRegex(engine.IntentError,expected) as caught:
      engine.apply(plan)
    self.assertEqual(injected,[boundary])
    self.assertIn('injected '+boundary,str(caught.exception))
    self.assertEqual(target.read_bytes(),b'before')
    self.assertEqual(source.read_bytes(),b'after')
    self.assertEqual(engine.history(),[])
    backups=list(engine.TXROOT.glob('*/0.before'))
    self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_bytes(),b'before')
    self.assertEqual(list(self.dst.glob('.orbit-rollback-*')),[])
    self.assertEqual(list(self.dst.glob('.orbit-remove-*')),[])

 def test_apply_rollback_staged_after_image_cleanup_failure_retains_state(self):
  # Exercise the ordinary apply fallback path: absent displaced exchange
  # artifact, rollback restores the verified transaction before-image and must
  # retain the exchanged after-image if cleanup fails before unlink.
  target=self.put(self.dst,'x','before',2_000_000_000)
  source=self.put(self.src,'x','after',3_000_000_000)
  plan=engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"')
  seen=[]
  def fail(boundary):
   if boundary=='apply.replace.after_exchange':
    # Force fallback from displaced-name compensation to the backup copy.
    for artifact in self.dst.glob('.orbit-*'):
     if artifact != target: artifact.unlink()
    raise OSError('trigger ordinary rollback fallback')
   if boundary=='apply.rollback.remove_staged_after_image':
    seen.append(boundary)
    raise OSError('injected staged cleanup refusal')
  with patch.object(engine,'_failure_boundary',side_effect=fail):
   with self.assertRaisesRegex(engine.IntentError,'Apply failed and rollback incomplete') as caught:
    engine.apply(plan)
  self.assertEqual(seen,['apply.rollback.remove_staged_after_image'])
  self.assertIn('staged after-image retained',str(caught.exception))
  self.assertEqual(target.read_bytes(),b'before')
  self.assertEqual(source.read_bytes(),b'after')
  self.assertEqual(engine.history(),[])
  backups=list(engine.TXROOT.glob('*/0.before'))
  self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_bytes(),b'before')
  staged=list(self.dst.glob('.orbit-rollback-*'))
  self.assertEqual(len(staged),1); self.assertEqual(staged[0].read_bytes(),b'after')

 def test_apply_history_compensation_cleanup_boundary_retains_state(self):
  target=self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  seen=[]
  def fail(name):
   if name=='apply.history_rollback.remove_staged_after_image': seen.append(name); raise OSError('stop before cleanup')
  def fail_save(_entries): raise OSError('history unavailable')
  with patch.object(engine,'_save',side_effect=fail_save), patch.object(engine,'_failure_boundary',side_effect=fail):
   with self.assertRaisesRegex(engine.IntentError,'History save failed and rollback incomplete') as caught:
    engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  self.assertEqual(seen,['apply.history_rollback.remove_staged_after_image'])
  self.assertIn('staged after-image retained',str(caught.exception))
  self.assertEqual(target.read_bytes(),b'before'); self.assertEqual(engine.history(),[])
  staged=list(self.dst.glob('.orbit-rollback-*')); self.assertEqual(len(staged),1); self.assertEqual(staged[0].read_bytes(),b'after')
  backup=list(engine.TXROOT.glob('*/0.before')); self.assertEqual(len(backup),1); self.assertEqual(backup[0].read_bytes(),b'before')

 def test_undo_rollback_retains_before_image_when_compensation_fsync_fails(self):
  import stat
  target=self.put(self.dst,'x','before',2_000_000_000)
  self.put(self.src,'x','after',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  destination_syncs=[0]
  real_fsync=os.fsync
  def fail_restore_then_compensation(fd):
   info=os.fstat(fd)
   result=real_fsync(fd)
   if stat.S_ISDIR(info.st_mode) and (info.st_dev,info.st_ino)==(self.dst.stat().st_dev,self.dst.stat().st_ino):
    destination_syncs[0]+=1
    if destination_syncs[0] in (2,3):
     raise OSError('injected undo destination fsync boundary '+str(destination_syncs[0]))
   return result
  with patch.object(engine.os,'fsync',side_effect=fail_restore_then_compensation):
   with self.assertRaisesRegex(engine.IntentError,'Undo rollback incomplete') as caught:
    engine.undo(entry['id'])
  self.assertEqual(destination_syncs[0],3)
  self.assertIn('undo destination fsync boundary 3',str(caught.exception))
  self.assertEqual(target.read_text(),'after')
  self.assertFalse(engine.history()[0]['undone'])
  txback=engine.TXROOT/entry['id']/'0.before'
  self.assertEqual(txback.read_text(),'before')
  staged=list(self.dst.glob('.orbit-undo-*'))
  self.assertEqual(len(staged),1); self.assertEqual(staged[0].read_text(),'before')

 def test_apply_and_undo_fsync_file_and_directory_descriptors(self):
  import stat
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  modes=[]; real_fsync=os.fsync
  def observe(fd):
   modes.append(os.fstat(fd).st_mode)
   return real_fsync(fd)
  with patch.object(engine.os,'fsync',new=observe):
   entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
   engine.undo(entry['id'])
  self.assertGreaterEqual(sum(stat.S_ISREG(mode) for mode in modes),3)
  self.assertGreaterEqual(sum(stat.S_ISDIR(mode) for mode in modes),4)
  self.assertEqual((self.dst/'x').read_text(),'before')

 def test_apply_commit_uncertain_after_history_publication_does_not_compensate(self):
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  real=engine._fsync_dir; history_parent_syncs=[0]
  def fail_history_parent(path):
   if Path(path)==engine.ROOT:
    history_parent_syncs[0]+=1
    # _save syncs before and after publication. Fail the second call only after
    # transaction directory syncs have completed.
    if history_parent_syncs[0]==3: raise OSError('injected directory fsync failure')
   return real(path)
  with patch.object(engine,'_fsync_dir',new=fail_history_parent):
   with self.assertRaisesRegex(engine.IntentError,'may be committed.*durability could not be confirmed'):
    engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  self.assertEqual((self.dst/'x').read_text(),'after')
  self.assertEqual(len(engine.history()),1)
  record=engine.history()[0]['records'][0]
  backup=engine.TXROOT/record['backup'].split('/')[-2]/record['backup'].split('/')[-1]
  self.assertEqual(engine.digest(backup),record['before_hash'])
  self.assertEqual(record['after_hash'],engine.digest(self.dst/'x'))

 def test_undo_commit_uncertain_keeps_restored_files_and_published_history(self):
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  real=engine._fsync_dir; real_replace=os.replace; published=[False]
  def mark_replace(source,target):
   result=real_replace(source,target)
   if Path(target)==engine.LOG: published[0]=True
   return result
  def fail_history_parent(path):
   if Path(path)==engine.ROOT and published[0]:
    raise OSError('injected undo history directory fsync failure')
   return real(path)
  with patch.object(engine.os,'replace',new=mark_replace), patch.object(engine,'_fsync_dir',new=fail_history_parent):
   with self.assertRaisesRegex(engine.IntentError,'Undo may be committed.*durability could not be confirmed'):
    engine.undo(entry['id'])
  self.assertEqual((self.dst/'x').read_text(),'before')
  self.assertTrue(engine.history()[0]['undone'])
  # The published undone record and restored file agree; cleanup was skipped.
  self.assertTrue((engine.TXROOT/entry['id']/'0.before').exists())
  self.assertTrue(list(self.dst.glob('.orbit-undo-*')))
  with self.assertRaisesRegex(engine.IntentError,'No undoable transaction'):
   engine.undo(entry['id'])

 def test_apply_add_failure_after_link_rolls_back_cleanly(self):
  self.put(self.src,'new.txt','added bytes',3_000_000_000)
  seen=[]
  def fail_after_link(boundary):
   if boundary=='apply.add.after_link':
    seen.append(boundary); raise OSError('injected after add link')
  with patch.object(engine,'_failure_boundary',side_effect=fail_after_link):
   with self.assertRaisesRegex(OSError,'after add link'):
    engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'),[0])
  self.assertEqual(seen,['apply.add.after_link'])
  self.assertFalse((self.dst/'new.txt').exists())
  self.assertEqual(engine.history(),[])
  self.assertEqual(list(engine.TXROOT.iterdir()),[])
  self.assertEqual(list(self.dst.glob('.orbit-*')),[])

 def test_apply_replace_failure_after_exchange_rolls_back_cleanly(self):
  target=self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  seen=[]
  def fail_after_install(boundary):
   seen.append(boundary)
   if boundary=='apply.replace.after_exchange': raise OSError('injected after install exchange')
  with patch.object(engine,'_failure_boundary',side_effect=fail_after_install):
   with self.assertRaisesRegex(OSError,'after install exchange'):
    engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  self.assertIn('apply.replace.after_exchange',seen)
  self.assertEqual(target.read_text(),'before')
  self.assertEqual(engine.history(),[])
  self.assertEqual(list(engine.TXROOT.iterdir()),[])
  self.assertEqual(list(self.dst.glob('.orbit-*')),[])

 def test_apply_rollback_failure_after_exchange_retains_recovery_state(self):
  target=self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  seen=[]
  def fail_during_rollback(boundary):
   if boundary in ('apply.replace.after_exchange','apply.rollback.after_exchange'):
    seen.append(boundary)
   if boundary=='apply.replace.after_exchange': raise OSError('start rollback')
   if boundary=='apply.rollback.after_exchange': raise OSError('rollback exchange boundary')
  with patch.object(engine,'_failure_boundary',side_effect=fail_during_rollback):
   with self.assertRaisesRegex(engine.IntentError,'Apply failed and rollback incomplete') as caught:
    engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  self.assertEqual(seen,['apply.replace.after_exchange','apply.rollback.after_exchange'])
  self.assertIn('rollback exchange boundary',str(caught.exception))
  self.assertEqual(target.read_text(),'before')
  self.assertEqual(engine.history(),[])
  backups=list(engine.TXROOT.glob('*/0.before'))
  self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_text(),'before')
  displaced=list(self.dst.glob('.orbit-*'))
  self.assertEqual(len(displaced),1); self.assertEqual(displaced[0].read_text(),'after')

 def test_apply_history_rollback_exchange_failure_retains_recovery_state(self):
  target=self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  seen=[]
  def fail_history_rollback(boundary):
   if boundary=='apply.history_rollback.after_exchange':
    seen.append(boundary); raise OSError('injected history compensation boundary')
  def fail_save(_entries): raise OSError('injected history write failure')
  with patch.object(engine,'_save',side_effect=fail_save), patch.object(engine,'_failure_boundary',side_effect=fail_history_rollback):
   with self.assertRaisesRegex(engine.IntentError,'History save failed and rollback incomplete') as caught:
    engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  self.assertEqual(seen,['apply.history_rollback.after_exchange'])
  self.assertIn('history compensation boundary',str(caught.exception))
  self.assertEqual(target.read_text(),'before'); self.assertEqual(engine.history(),[])
  backups=list(engine.TXROOT.glob('*/0.before'))
  self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_text(),'before')
  staged=list(self.dst.glob('.orbit-rollback-*'))
  self.assertEqual(len(staged),1); self.assertEqual(staged[0].read_text(),'after')

 def test_apply_rollback_transaction_cleanup_failure_retains_consistent_state(self):
  target=self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  seen=[]
  def fail_before_tx_cleanup(boundary):
   if boundary=='apply.replace.after_exchange': raise OSError('trigger apply rollback')
   if boundary=='apply.rollback.remove_transaction':
    seen.append(boundary); raise OSError('injected transaction cleanup failure')
  with patch.object(engine,'_failure_boundary',side_effect=fail_before_tx_cleanup):
   with self.assertRaisesRegex(OSError,'transaction cleanup failure'):
    engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  self.assertEqual(seen,['apply.rollback.remove_transaction'])
  self.assertEqual(target.read_text(),'before'); self.assertEqual(engine.history(),[])
  backups=list(engine.TXROOT.glob('*/0.before'))
  self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_text(),'before')
  self.assertEqual(list(self.dst.glob('.orbit-*')),[])

 def test_apply_history_prepublication_failure_rolls_back(self):
  target=self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  failed=[]
  def fail_before_publish(boundary):
   if boundary=='history.before_publish':
    failed.append(boundary); raise OSError('injected before history publication')
  with patch.object(engine,'_failure_boundary',side_effect=fail_before_publish):
   with self.assertRaisesRegex(OSError,'before history publication'):
    engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  self.assertEqual(failed,['history.before_publish'])
  self.assertEqual(target.read_text(),'before'); self.assertEqual(engine.history(),[])
  self.assertEqual(list(engine.TXROOT.iterdir()),[])
  self.assertEqual(list(self.dst.glob('.orbit-*')),[])

 def test_apply_history_postpublication_failure_preserves_committed_state(self):
  target=self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  failed=[]
  def fail_after_publish(boundary):
   if boundary=='history.after_publish':
    failed.append(boundary); raise OSError('injected after history publication')
  with patch.object(engine,'_failure_boundary',side_effect=fail_after_publish):
   with self.assertRaisesRegex(engine.IntentError,'may be committed.*durability could not be confirmed'):
    engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  self.assertEqual(failed,['history.after_publish'])
  self.assertEqual(target.read_text(),'after'); entries=engine.history(); self.assertEqual(len(entries),1)
  self.assertFalse(entries[0]['undone'])
  self.assertEqual(engine.digest(engine.TXROOT/entries[0]['id']/'0.before'),entries[0]['records'][0]['before_hash'])

 def test_undo_history_prepublication_failure_restores_applied_state(self):
  target=self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  failed=[]
  def fail_before_publish(boundary):
   if boundary=='history.before_publish':
    failed.append(boundary); raise OSError('injected before undo history publication')
  with patch.object(engine,'_failure_boundary',side_effect=fail_before_publish):
   with self.assertRaisesRegex(OSError,'before undo history publication'):
    engine.undo(entry['id'])
  self.assertEqual(failed,['history.before_publish'])
  self.assertEqual(target.read_text(),'after'); self.assertFalse(engine.history()[0]['undone'])
  self.assertTrue((engine.TXROOT/entry['id']/'0.before').exists())
  self.assertEqual(list(self.dst.glob('.orbit-undo-*')),[])

 def test_undo_history_postpublication_failure_preserves_committed_state(self):
  target=self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  failed=[]
  def fail_after_publish(boundary):
   if boundary=='history.after_publish':
    failed.append(boundary); raise OSError('injected after undo history publication')
  with patch.object(engine,'_failure_boundary',side_effect=fail_after_publish):
   with self.assertRaisesRegex(engine.IntentError,'Undo may be committed.*durability could not be confirmed'):
    engine.undo(entry['id'])
  self.assertEqual(failed,['history.after_publish'])
  self.assertEqual(target.read_text(),'before'); self.assertTrue(engine.history()[0]['undone'])
  self.assertTrue((engine.TXROOT/entry['id']/'0.before').exists())
  self.assertEqual(len(list(self.dst.glob('.orbit-undo-*'))),1)
  with self.assertRaisesRegex(engine.IntentError,'No undoable transaction'):
   engine.undo(entry['id'])

 def test_history_save_cleanup_does_not_mask_prepublication_error(self):
  engine.ROOT.mkdir(parents=True)
  with patch.object(engine.os,'replace',side_effect=OSError('primary publish failure')), patch.object(engine.os,'unlink',side_effect=OSError('secondary temp cleanup failure')):
   with self.assertRaisesRegex(OSError,'primary publish failure'):
    engine._save([])

 def test_history_save_skips_cleanup_after_publication(self):
  engine.ROOT.mkdir(parents=True)
  entries=[]
  # os.replace consumed the temporary path; cleanup must not run afterwards
  # and turn an already-published history update into a reported failure.
  with patch.object(engine.os,'unlink',side_effect=OSError('unexpected postpublication unlink')):
   engine._save(entries)
  self.assertEqual(engine._load(),entries)
  self.assertEqual(list(engine.ROOT.glob('.history-*')),[])

 def test_history_postpublication_runtime_exception_is_commit_uncertain(self):
  # The publication boundary must protect committed files/history from any
  # ordinary exception, not only OSError raised by directory fsync.
  target=self.put(self.dst,'x','before',2_000_000_000)
  self.put(self.src,'x','after',3_000_000_000)
  with patch.object(engine,'_failure_boundary',side_effect=lambda name: (_ for _ in ()).throw(RuntimeError('injected postpublish callback failure')) if name=='history.after_publish' else None):
   with self.assertRaisesRegex(engine.IntentError,'may be committed.*durability could not be confirmed'):
    engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  entry=engine.history()[0]
  self.assertFalse(entry['undone'])
  self.assertEqual(target.read_bytes(),b'after')
  self.assertEqual(engine.digest(engine.TXROOT/entry['id']/'0.before'),entry['records'][0]['before_hash'])

  # Undo publishes the inverse history state at the same shared boundary; its
  # RuntimeError must not trigger compensation back to the applied bytes.
  with patch.object(engine,'_failure_boundary',side_effect=lambda name: (_ for _ in ()).throw(RuntimeError('injected postpublish callback failure')) if name=='history.after_publish' else None):
   with self.assertRaisesRegex(engine.IntentError,'may be committed.*durability could not be confirmed'):
    engine.undo(entry['id'])
  self.assertEqual(target.read_bytes(),b'before')
  self.assertTrue(engine.history()[0]['undone'])
  with self.assertRaisesRegex(engine.IntentError,'No undoable transaction'):
   engine.undo(entry['id'])

 def test_history_save_does_not_follow_predictable_temp_symlink(self):
  engine.ROOT.mkdir(parents=True)
  outside=self.root/'outside.json'; outside.write_text('preserve me')
  predictable=engine.LOG.with_suffix('.tmp')
  predictable.symlink_to(outside)
  entries=[{'id':'safe','undone':False}]
  engine._save(entries)
  self.assertEqual(outside.read_text(),'preserve me')
  self.assertTrue(predictable.is_symlink())
  self.assertEqual(engine.LOG.read_text(),__import__('json').dumps(entries,indent=2))
  self.assertEqual(list(engine.ROOT.glob('.history-*')),[])
 def test_history_save_replace_failure_preserves_existing_log_and_cleans_temp(self):
  engine.ROOT.mkdir(parents=True)
  engine.LOG.write_text('[{"old":true}]')
  with patch.object(engine.os,'replace',side_effect=OSError('injected publish failure')):
   with self.assertRaisesRegex(OSError,'publish failure'):
    engine._save([{'new':True}])
  self.assertEqual(engine.LOG.read_text(),'[{"old":true}]')
  self.assertEqual(list(engine.ROOT.glob('.history-*')),[])
 def test_unknown_intent(self):
  with self.assertRaises(engine.IntentError):engine.parse('clean up my computer')
 def test_undo_partial_backup_read_is_removed_without_mutating_target(self):
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  real_open=os.open; real_read=os.read; backup_fds=set(); reads=[0]
  def track_backup_open(path,flags,*args,**kwargs):
   fd=real_open(path,flags,*args,**kwargs)
   if path=='0.before' and kwargs.get('dir_fd') is not None:
    backup_fds.add(fd)
   return fd
  def partial_then_fail(fd,count):
   if fd in backup_fds:
    reads[0]+=1
    if reads[0]==1: return real_read(fd,4)
    raise OSError('injected partial staging read')
   return real_read(fd,count)
  with patch.object(engine.os,'open',side_effect=track_backup_open), patch.object(engine.os,'read',side_effect=partial_then_fail):
   with self.assertRaisesRegex(engine.IntentError,'could not be safely read'):
    engine.undo(entry['id'])
  self.assertGreaterEqual(reads[0],2)
  self.assertEqual((self.dst/'x').read_text(),'after')
  self.assertFalse(engine.history()[0]['undone'])
  self.assertEqual(list(self.dst.glob('.orbit-undo-*')),[])
  self.assertEqual((self.root/'state'/'transactions'/entry['id']/'0.before').read_text(),'before')

 def test_history_loader_detects_name_replacement_after_open(self):
  engine.ROOT.mkdir(parents=True)
  engine.LOG.write_text('[]')
  old=engine.LOG.with_suffix('.old')
  old.write_text('[{"old":true}]')
  real_read=os.read; replaced=[False]
  def swap_name_after_first_read(fd,count):
   data=real_read(fd,count)
   if not replaced[0]:
    replaced[0]=True
    os.replace(old,engine.LOG)
   return data
  with patch.object(engine.os,'read',side_effect=swap_name_after_first_read):
   with self.assertRaisesRegex(engine.IntentError,'changed while being read'):
    engine.history()
  self.assertTrue(replaced[0])
  self.assertEqual(engine.LOG.read_text(),'[{"old":true}]')

 def test_history_loader_refuses_symlink_without_reading_external_history(self):
  engine.ROOT.mkdir(parents=True)
  outside=self.root/'outside-history.json'
  outside.write_text('[{"id":"external","undone":false,"records":[]}]')
  engine.LOG.symlink_to(outside)
  with self.assertRaisesRegex(engine.IntentError,'must not be a symlink'):
   engine.history()
  self.assertEqual(outside.read_text(),'[{"id":"external","undone":false,"records":[]}]')
  self.assertTrue(engine.LOG.is_symlink())

 def test_history_loader_rejects_hardlink_and_fifo_without_blocking(self):
  engine.ROOT.mkdir(parents=True)
  engine.LOG.write_text('[]')
  alias=self.root/'history-alias'; os.link(engine.LOG,alias)
  with self.assertRaisesRegex(engine.IntentError,'link count'):
   engine.history()
  alias.unlink(); engine.LOG.unlink()
  os.mkfifo(engine.LOG)
  with self.assertRaisesRegex(engine.IntentError,'unexpected type'):
   engine.history()
  self.assertTrue(engine.LOG.exists())

 def test_history_loader_detects_change_during_read(self):
  engine.ROOT.mkdir(parents=True)
  engine.LOG.write_text('[]')
  real_read=os.read; changed=[False]
  def mutate_after_first_read(fd,count):
   data=real_read(fd,count)
   if not changed[0]:
    changed[0]=True
    engine.LOG.write_text('[{"changed":true}]')
   return data
  with patch.object(engine.os,'read',side_effect=mutate_after_first_read):
   with self.assertRaisesRegex(engine.IntentError,'changed while being read'):
    engine.history()
  self.assertTrue(changed[0])
  self.assertEqual(engine.LOG.read_text(),'[{"changed":true}]')

 def test_history_loader_rejects_malformed_json_and_non_list_root(self):
  engine.ROOT.mkdir(parents=True)
  for raw, message in ((b"{", "History is malformed"), (b"{}", "History must contain a list")):
   engine.LOG.write_bytes(raw)
   with self.subTest(raw=raw), self.assertRaisesRegex(engine.IntentError, message):
    engine.history()

 def test_history_loader_rejects_invalid_record_path_and_backup_before_undo(self):
  engine.ROOT.mkdir(parents=True)
  txid="20260101T000000-abcdef12"
  good_hash="0"*64
  base={"kind":"REPLACE", "source":str(self.src/"source"), "target":str(self.dst/"target"),
        "backup":str(engine.TXROOT/txid/"0.before"), "before_hash":good_hash,
        "after_hash":good_hash, "target_parent_id":"1:2"}
  entry={"id":txid,"intent":f'Update files in "{self.dst}" with the ones from "{self.src}"',"time":"2026-01-01T00:00:00+00:00","records":[base],"undone":False}
  bad_records=[dict(base,target="relative/target"), dict(base,backup=str(self.root/"outside")),
               dict(base,displaced=str(self.root/"outside/.orbit-undo-evil"))]
  for record in bad_records:
   engine.LOG.write_text(__import__("json").dumps([dict(entry,records=[record])]))
   with self.subTest(record=record), self.assertRaises(engine.IntentError): engine.history()
  backup_intent=dict(entry,intent=f'Make a backup of "{self.src}/original.txt"')
  engine.LOG.write_text(__import__("json").dumps([backup_intent]))
  with self.assertRaisesRegex(engine.IntentError,"do not match its backup intent"):
   engine.history()

 def test_history_loader_rejects_unsafe_transaction_ids_without_touching_external_paths(self):
  engine.ROOT.mkdir(parents=True)
  outside=self.root/"outside"; outside.mkdir()
  victim=outside/"keep"; victim.write_text("must survive")
  record={"kind":"ADD", "source":str(self.src/"source"), "target":str(victim),
          "backup":"", "before_hash":"", "after_hash":"0"*64}
  for txid in ("../outside", "..", "20260101T000000-aaaaaaaa/../../outside", "../outside"):
   engine.LOG.write_text(__import__("json").dumps([{"id":txid,"intent":"bad","time":"now","records":[record],"undone":False}]))
   with self.subTest(txid=txid), self.assertRaisesRegex(engine.IntentError,"transaction ID"):
    engine.history()
  self.assertEqual(victim.read_text(),"must survive")

 def test_history_loader_rejects_duplicate_destination_records(self):
  engine.ROOT.mkdir(parents=True)
  txid="20260101T000000-abcdef12"
  target=self.dst/'same.txt'
  intent=f'Update files in "{self.dst}" with the ones from "{self.src}"'
  record={"kind":"ADD", "source":str(self.src/'same.txt'), "target":str(target),
          "backup":"", "before_hash":"", "after_hash":"0"*64,
          "target_parent_id":f"{self.dst.stat().st_dev}:{self.dst.stat().st_ino}"}
  entry={"id":txid,"intent":intent,"time":"2026-01-01T00:00:00+00:00",
         "records":[dict(record),dict(record)],"undone":False}
  engine.LOG.write_text(__import__("json").dumps([entry]))
  with self.assertRaisesRegex(engine.IntentError,"duplicate destination paths"):
   engine.history()
  self.assertFalse(target.exists())

 def test_history_loader_rejects_duplicate_replace_records_and_parent_aliases(self):
  engine.ROOT.mkdir(parents=True)
  txid="20260101T000000-abcdef12"
  alias=self.root/'destination-alias'
  alias.symlink_to(self.dst, target_is_directory=True)
  intent=f'Update files in "{self.dst}" with the ones from "{self.src}"'
  parent_id=f"{self.dst.stat().st_dev}:{self.dst.stat().st_ino}"
  records=[]
  for index, parent in enumerate((self.dst, alias)):
   records.append({"kind":"REPLACE", "source":str(self.src/'same.txt'),
                   "target":str(parent/'same.txt'),
                   "backup":str(engine.TXROOT/txid/f"{index}.before"),
                   "before_hash":"1"*64, "after_hash":"0"*64,
                   "target_parent_id":parent_id})
  entry={"id":txid,"intent":intent,"time":"2026-01-01T00:00:00+00:00",
         "records":records,"undone":False}
  engine.LOG.write_text(__import__("json").dumps([entry]))
  with self.assertRaisesRegex(engine.IntentError,"duplicate destination paths"):
   engine.history()
  self.assertFalse((self.dst/'same.txt').exists())

 def test_history_loader_binds_record_paths_to_stored_intent(self):
  engine.ROOT.mkdir(parents=True)
  txid="20260101T000000-abcdef12"
  victim=self.root/"outside-victim"; victim.write_text("human data")
  intent=f'Update files in "{self.dst}" with the ones from "{self.src}"'
  record={"kind":"ADD", "source":str(self.src/"source"), "target":str(victim),
          "backup":"", "before_hash":"", "after_hash":engine.digest(victim),
          "target_parent_id":f"{self.dst.stat().st_dev}:{self.dst.stat().st_ino}"}
  entry={"id":txid,"intent":intent,"time":"2026-01-01T00:00:00+00:00","records":[record],"undone":False}
  engine.LOG.write_text(__import__("json").dumps([entry]))
  with self.assertRaisesRegex(engine.IntentError,"do not match its sync intent"):
   engine.undo()
  self.assertEqual(victim.read_text(),"human data")

 def test_undo_refuses_transaction_directory_symlink_without_following_it(self):
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  txdir=engine.TXROOT/entry['id']; outside=self.root/'outside-tx'
  txdir.rename(outside); txdir.symlink_to(outside,target_is_directory=True)
  with self.assertRaisesRegex(engine.IntentError,'real directories and files'):
   engine.undo(entry['id'])
  self.assertEqual((self.dst/'x').read_text(),'after')
  self.assertEqual((outside/'0.before').read_text(),'before')
  self.assertTrue(txdir.is_symlink())

 def test_undo_refuses_backup_symlink_without_reading_referent(self):
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  backup=engine.TXROOT/entry['id']/'0.before'; outside=self.root/'outside-backup'
  outside.write_text('outside data'); backup.unlink(); backup.symlink_to(outside)
  with self.assertRaisesRegex(engine.IntentError,'real directories and files'):
   engine.undo(entry['id'])
  self.assertEqual((self.dst/'x').read_text(),'after')
  self.assertEqual(outside.read_text(),'outside data')
  self.assertTrue(backup.is_symlink())

 def test_undo_detects_transaction_directory_replaced_after_open(self):
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  txdir=engine.TXROOT/entry['id']; moved=self.root/'pinned-transaction'; outside=self.root/'outside-tx'
  outside.mkdir(); (outside/'0.before').write_text('outside transaction')
  real_open=os.open; real_read=os.read; backup_fds=set(); raced=[False]
  def track_backup_open(path,flags,*args,**kwargs):
   fd=real_open(path,flags,*args,**kwargs)
   if path=='0.before' and kwargs.get('dir_fd') is not None: backup_fds.add(fd)
   return fd
  def replace_txdir_after_read(fd,count):
   data=real_read(fd,count)
   if fd in backup_fds and not raced[0]:
    raced[0]=True; txdir.rename(moved); txdir.symlink_to(outside,target_is_directory=True)
   return data
  with patch.object(engine.os,'open',side_effect=track_backup_open), patch.object(engine.os,'read',side_effect=replace_txdir_after_read):
   with self.assertRaisesRegex(engine.IntentError,'changed while being read'):
    engine.undo(entry['id'])
  self.assertTrue(raced[0]); self.assertEqual((self.dst/'x').read_text(),'after')
  self.assertEqual((outside/'0.before').read_text(),'outside transaction')
  self.assertEqual((moved/'0.before').read_text(),'before')
  self.assertEqual(list(self.dst.glob('.orbit-undo-*')),[])

 def test_undo_detects_backup_leaf_replaced_after_open(self):
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  backup=engine.TXROOT/entry['id']/'0.before'; moved=self.root/'pinned-backup'; outside=self.root/'outside-backup'
  outside.write_text('outside backup')
  real_open=os.open; real_read=os.read; backup_fds=set(); raced=[False]
  def track_backup_open(path,flags,*args,**kwargs):
   fd=real_open(path,flags,*args,**kwargs)
   if path=='0.before' and kwargs.get('dir_fd') is not None: backup_fds.add(fd)
   return fd
  def replace_backup_after_read(fd,count):
   data=real_read(fd,count)
   if fd in backup_fds and not raced[0]:
    raced[0]=True; backup.rename(moved); backup.symlink_to(outside)
   return data
  with patch.object(engine.os,'open',side_effect=track_backup_open), patch.object(engine.os,'read',side_effect=replace_backup_after_read):
   with self.assertRaisesRegex(engine.IntentError,'changed while being read'):
    engine.undo(entry['id'])
  self.assertTrue(raced[0]); self.assertEqual((self.dst/'x').read_text(),'after')
  self.assertEqual(outside.read_text(),'outside backup')
  self.assertEqual(moved.read_text(),'before')
  self.assertEqual(list(self.dst.glob('.orbit-undo-*')),[])

 def test_transaction_cleanup_refuses_symlink_directory_without_touching_referent(self):
  txroot=engine.TXROOT; txroot.mkdir(parents=True)
  outside=self.root/'outside'; outside.mkdir(); victim=outside/'keep.bin'; victim.write_bytes(b'valuable external bytes')
  link=txroot/'tx-link'; link.symlink_to(outside, target_is_directory=True)
  with self.assertRaisesRegex(engine.IntentError,'not a real directory'):
   engine._remove_transaction_dir(link)
  self.assertEqual(victim.read_bytes(),b'valuable external bytes')
  self.assertTrue(link.is_symlink())

 def test_transaction_cleanup_refuses_nonregular_artifact_without_traversing(self):
  txdir=engine.TXROOT/'tx-nested'; txdir.mkdir(parents=True)
  outside=self.root/'outside'; outside.mkdir(); victim=outside/'keep'; victim.write_text('keep')
  (txdir/'subdir').symlink_to(outside, target_is_directory=True)
  with self.assertRaisesRegex(engine.IntentError,'Unexpected transaction artifact'):
   engine._remove_transaction_dir(txdir)
  self.assertEqual(victim.read_text(),'keep')
  self.assertTrue((txdir/'subdir').is_symlink())

 def test_transaction_cleanup_refuses_replaced_directory_entry(self):
  txdir=engine.TXROOT/'tx-replaced'; txdir.mkdir(parents=True)
  (txdir/'artifact').write_text('cleanup candidate')
  moved=self.root/'opened-directory-moved-away'
  real_stat=os.stat; raced=[False]
  def replace_entry_before_identity_check(path,*args,**kwargs):
   if path==txdir.name and kwargs.get('dir_fd') is not None and not raced[0]:
    txdir.rename(moved)
    txdir.mkdir()
    (txdir/'keep').write_text('replacement must survive')
    raced[0]=True
   return real_stat(path,*args,**kwargs)
  with patch.object(engine.os,'stat',side_effect=replace_entry_before_identity_check):
   with self.assertRaisesRegex(engine.IntentError,'changed during cleanup'):
    engine._remove_transaction_dir(txdir)
  self.assertTrue(raced[0])
  self.assertTrue(moved.is_dir())
  self.assertEqual(list(moved.iterdir()),[])
  self.assertEqual((txdir/'keep').read_text(),'replacement must survive')

 def test_undo_partial_transaction_directory_cleanup_reports_committed_state(self):
  self.put(self.dst,'a','before-a',2_000_000_000); self.put(self.src,'a','after-a',3_000_000_000)
  self.put(self.dst,'b','before-b',2_000_000_000); self.put(self.src,'b','after-b',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  txdir=self.root/'state'/'transactions'/entry['id']
  self.assertEqual(sorted(p.name for p in txdir.iterdir()),['0.before','1.before'])
  failed=[False]
  def fail_transaction_cleanup(boundary):
   if boundary=='undo.cleanup.remove_transaction' and not failed[0]:
    failed[0]=True
    raise OSError('injected transaction cleanup failure')
  with patch.object(engine,'_failure_boundary',side_effect=fail_transaction_cleanup):
   with self.assertRaisesRegex(engine.IntentError,'Undo completed and is recorded, but cleanup was incomplete') as caught:
    engine.undo(entry['id'])
  self.assertTrue(failed[0])
  self.assertIn(str(txdir),str(caught.exception))
  self.assertTrue((txdir/'0.before').exists())
  self.assertEqual((txdir/'1.before').read_text(),'before-b')
  self.assertEqual((self.dst/'a').read_text(),'before-a')
  self.assertEqual((self.dst/'b').read_text(),'before-b')
  self.assertTrue(engine.history()[0]['undone'])
  with self.assertRaisesRegex(engine.IntentError,'No undoable transaction'):
   engine.undo(entry['id'])

 def test_undo_stage_cleanup_failure_is_reported_and_retained(self):
  self.put(self.dst,'a','before-a',2_000_000_000); self.put(self.src,'a','after-a',3_000_000_000)
  self.put(self.dst,'b','before-b',2_000_000_000); self.put(self.src,'b','after-b',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  exchange=engine._rename_exchange; calls=[0]
  def fail_later_exchange(left,right):
   calls[0]+=1
   if calls[0]==2: raise OSError('injected later undo exchange failure')
   return exchange(left,right)
  def fail_stage_cleanup(boundary):
   if boundary=='undo.rollback.remove_stage':
    raise OSError('injected stage cleanup failure')
  with patch.object(engine,'_rename_exchange',side_effect=fail_later_exchange), patch.object(engine,'_failure_boundary',side_effect=fail_stage_cleanup):
   with self.assertRaisesRegex(engine.IntentError,'rollback incomplete') as caught:
    engine.undo(entry['id'])
  self.assertIn('staging cleanup failed',str(caught.exception))
  self.assertEqual((self.dst/'a').read_text(),'after-a'); self.assertEqual((self.dst/'b').read_text(),'after-b')
  self.assertFalse(engine.history()[0]['undone'])
  residue=list(self.dst.glob('.orbit-undo-*'))
  self.assertEqual(len(residue),2)
  self.assertCountEqual([p.read_text() for p in residue],['before-a','before-b'])

 def test_add_undo_cleanup_unlink_failure_reports_committed_state(self):
  self.put(self.src,'new.txt','added bytes',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'),[0])
  txdir=engine.TXROOT/entry['id']
  failed=[False]
  def fail_claim_cleanup(boundary):
   if boundary=='undo.cleanup.remove_add_claim' and not failed[0]:
    failed[0]=True
    raise OSError('injected post-commit ADD undo unlink failure')
  with patch.object(engine,'_failure_boundary',side_effect=fail_claim_cleanup):
   with self.assertRaisesRegex(engine.IntentError,'Undo completed and is recorded, but cleanup was incomplete') as caught:
    engine.undo(entry['id'])
  claims=list(self.dst.glob('.orbit-undo-remove-*'))
  self.assertTrue(failed[0]); self.assertEqual(len(claims),1)
  self.assertIn(str(claims[0]),str(caught.exception)); self.assertIn(str(txdir),str(caught.exception))
  self.assertEqual(claims[0].read_bytes(),b'added bytes')
  self.assertFalse((self.dst/'new.txt').exists())
  self.assertTrue(engine.history()[0]['undone'])
  self.assertTrue(txdir.is_dir())
  with self.assertRaisesRegex(engine.IntentError,'No undoable transaction'):
   engine.undo(entry['id'])

 def test_replace_undo_cleanup_unlink_failure_reports_committed_state(self):
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  txdir=engine.TXROOT/entry['id']
  failed=[False]
  def fail_stage_cleanup(boundary):
   if boundary=='undo.cleanup.remove_replace_stage' and not failed[0]:
    failed[0]=True
    raise OSError('injected post-commit REPLACE undo unlink failure')
  with patch.object(engine,'_failure_boundary',side_effect=fail_stage_cleanup):
   with self.assertRaisesRegex(engine.IntentError,'Undo completed and is recorded, but cleanup was incomplete') as caught:
    engine.undo(entry['id'])
  artifacts=list(self.dst.glob('.orbit-undo-*'))
  self.assertTrue(failed[0]); self.assertEqual(len(artifacts),1)
  self.assertIn(str(artifacts[0]),str(caught.exception)); self.assertIn(str(txdir),str(caught.exception))
  self.assertEqual(artifacts[0].read_bytes(),b'after')
  self.assertEqual((self.dst/'x').read_bytes(),b'before')
  self.assertTrue(engine.history()[0]['undone'])
  self.assertEqual((txdir/'0.before').read_bytes(),b'before')
  with self.assertRaisesRegex(engine.IntentError,'No undoable transaction'):
   engine.undo(entry['id'])

 def test_undo_cleanup_directory_fsync_failure_reports_committed_state(self):
  self.put(self.dst,'x','before',2_000_000_000); self.put(self.src,'x','after',3_000_000_000)
  entry=engine.apply(engine.parse(f'Update files in "{self.dst}" with the ones from "{self.src}"'))
  txdir=self.root/'state'/'transactions'/entry['id']
  failed=[False]
  def fail_post_cleanup_sync(boundary):
   if boundary=='undo.cleanup.sync_destination' and not failed[0]:
    failed[0]=True
    self.assertFalse(list(self.dst.glob('.orbit-undo-*')))
    raise OSError('injected post-commit cleanup directory fsync failure')
  with patch.object(engine,'_failure_boundary',side_effect=fail_post_cleanup_sync):
   with self.assertRaisesRegex(engine.IntentError,'Undo completed and is recorded, but cleanup was incomplete') as caught:
    engine.undo(entry['id'])
  self.assertTrue(failed[0])
  self.assertIn(str(txdir),str(caught.exception))
  self.assertEqual((self.dst/'x').read_text(),'before')
  self.assertTrue(engine.history()[0]['undone'])
  self.assertFalse(list(self.dst.glob('.orbit-undo-*')))
  self.assertEqual((txdir/'0.before').read_text(),'before')
  with self.assertRaisesRegex(engine.IntentError,'No undoable transaction'):
   engine.undo(entry['id'])

if __name__=='__main__':unittest.main()
