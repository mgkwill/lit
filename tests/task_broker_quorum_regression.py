"""Regression coverage for quorum ownership and GitHub-enforced fencing."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import broker_admission as admission
import github_queue_coordinator as queue
import task_broker_policy_observer as observer
import task_broker_quorum_backup as backup
import task_broker_replica as replica
from task_broker_quorum import EtcdStore, QuorumAuthority, Record, Rejected, ReplicaUnavailable

BINDING={'repo':'100latent/lit-merge-queue-sandbox','main_ruleset':10,'freeze_ruleset':11}


class Store:
    def __init__(self,cluster_id='1'):
        self.values={}
        self.cluster_id=cluster_id
        self.lock=threading.Lock()
        self.revision=0
        self.online=True

    def read(self,*names):
        with self.lock:
            if not self.online:
                raise ReplicaUnavailable('fixture quorum down')
            return {name:self.values[name] for name in names if name in self.values}

    def read_with_cluster(self,*names):
        return self.read(*names), self.cluster_id

    def change(self,records,changes):
        with self.lock:
            if not self.online:
                raise ReplicaUnavailable('fixture quorum down')
            if any(self.values.get(name)!=record for name,record in records.items()):
                return False
            self.revision+=1
            for name,value in changes.items():
                if value is None:
                    self.values.pop(name,None)
                else:
                    self.values[name]=Record(self.revision,value)
            return True


class QuorumTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        with queue.database(self.root/'broker') as db, db:
            db.execute('CREATE TABLE publications(claim TEXT PRIMARY KEY, request TEXT, digest TEXT, phase TEXT, receipt TEXT)')
            admission.initialize(db)
            db.execute("INSERT INTO metadata VALUES ('manifest',?)",(queue.encoded(BINDING),))
        self.checkpoint=self.root/'broker'/'queue.sqlite'
        self.store=Store()
        self.revoked=set()
        self.minted=0
        self.applied=[]
        self.may_revoke=True
        self.entered=threading.Event()
        self.resume=threading.Event()
        self.old=self.authority(self.paused_github)
        self.alt=self.authority(self.github)
        self.initial=self.old.request('manager','/bootstrap',b'{"holder":"primary","epoch":7,"revision":4}\n'+
                                      self.checkpoint.read_bytes(),0,0)

    def authority(self,provider):
        return QuorumAuthority(self.root,BINDING,self.store,self.mint,lambda:('fixture-reader',100),
                               lambda:('fixture-broker-reader',100),provider,self.revoke,writer_timeout=0)

    def mint(self):
        self.minted+=1
        return 'fixture-writer-'+str(self.minted)

    def revoke(self,token):
        if not self.may_revoke:
            raise ReplicaUnavailable('fixture could not confirm revocation')
        self.revoked.add(token)

    def github(self,token):
        outer=self
        class GitHub:
            def mutate(self,kind,node,head):
                if token in outer.revoked:
                    raise queue.ApiError(401)
                outer.applied.append((token,kind,node,head))
                return {'accepted':True}
        return GitHub()

    def paused_github(self,token):
        self.entered.set()
        if not self.resume.wait(5):
            raise RuntimeError('fixture was not resumed')
        return self.github(token)

    def promote(self):
        return self.alt.request('manager','/promote',queue.encoded(
            {'from':'primary','to':'standby','epoch':7,'request':'a'*32}).encode(),0,0)

    def provider(self,authority,actor,epoch):
        return authority.request(actor,'/provider',queue.encoded(
            {'operation':'queue','kind':'enqueue','node':'PR_1','head':'b'*40}).encode(),epoch,4)

    def test_promotion_revokes_the_paused_sender_before_advancing_epoch(self):
        with ThreadPoolExecutor(1) as pool:
            write=pool.submit(self.provider,self.old,'primary',7)
            try:
                self.assertTrue(self.entered.wait(3))
                self.may_revoke=False
                with self.assertRaises(ReplicaUnavailable):
                    self.promote()
                self.assertEqual(self.alt.request('manager','/head',b'',0,0),self.initial)
                self.may_revoke=True
                promoted=self.promote()
                self.assertEqual(promoted,self.promote())
                self.assertEqual(promoted['epoch'],8)
                self.assertEqual(self.provider(self.alt,'standby',8),{'accepted':True})
            finally:
                self.resume.set()
            with self.assertRaises(ReplicaUnavailable):
                write.result(timeout=3)
        self.assertEqual(self.revoked,{'fixture-writer-1'})
        self.assertEqual([call[0] for call in self.applied],['fixture-writer-2'])
        with self.assertRaises(Rejected):
            self.provider(self.old,'primary',8)

    def test_quorum_loss_withholds_all_writes_and_does_not_mint(self):
        self.store.online=False
        with self.assertRaises(ReplicaUnavailable):
            self.provider(self.alt,'primary',7)
        self.assertEqual(self.minted,0)
        self.store.online=True
        self.assertEqual(self.alt.request('manager','/head',b'',0,0),self.initial)

    def test_an_abandoned_turn_is_revoked_before_the_same_actor_can_continue(self):
        self.store.change({'writer':None},{'writer':b'{"token":"abandoned","started":0}'})
        with self.assertRaises(ReplicaUnavailable):
            self.provider(self.alt,'primary',7)
        self.assertEqual(self.revoked,{'abandoned'})
        self.assertFalse(self.store.read('writer'))
        self.assertEqual(self.applied,[])
        self.assertEqual(self.provider(self.alt,'primary',7),{'accepted':True})

    def test_checkpoint_updates_preserve_digest_and_retry_identity(self):
        with closing(sqlite3.connect(self.checkpoint)) as db, db:
            db.execute("INSERT INTO metadata VALUES ('test','quorum')")
        raw=self.checkpoint.read_bytes()
        head=self.old.request('primary','/checkpoint',raw,7,4)
        self.assertEqual(head['revision'],5)
        self.assertEqual(self.alt.request('primary','/checkpoint',raw,7,4),head)
        self.assertEqual(self.alt.request('primary','/snapshot',b'',7,5),raw)

    def test_observer_fails_closed_when_rules_hide_bypass_actors(self):
        config=BINDING | {'publication_ruleset':12,'worker_installations':{}}
        with self.assertRaisesRegex(RuntimeError,'bypass visibility'):
            observer.observation(config,lambda path:{'id':int(path.split('/')[-1])})

    def test_backup_preserves_the_exact_revision_and_fences_the_old_epoch(self):
        path=self.root/'saved.lit-quorum'
        captured=backup.backup(self.store,BINDING,path)
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        self.assertEqual(captured['recovery_blockers'],[])
        self.assertEqual(captured['head'],self.initial)
        with self.assertRaises(FileExistsError):
            backup.backup(self.store,BINDING,path)
        target=Store('2')
        result=backup.restore(target,BINDING,path,self.initial,captured['archive_sha256'],'standby')
        self.assertEqual(result['head'],self.initial|{'holder':'standby','epoch':8})
        self.assertEqual(backup.restore(target,BINDING,path,self.initial,captured['archive_sha256'],'standby'),result)
        restored=QuorumAuthority(self.root,BINDING,target,self.mint,lambda:None,lambda:None,self.github)
        self.assertEqual(restored.request('standby','/snapshot',b'',8,4),self.checkpoint.read_bytes())
        with self.assertRaises(Rejected):
            restored.request('primary','/snapshot',b'',7,4)

    def test_restore_checks_watermark_checksum_and_replacement_cluster(self):
        path=self.root/'saved.lit-quorum'
        captured=backup.backup(self.store,BINDING,path)
        target=Store('2')
        with self.assertRaisesRegex(Rejected,'watermark'):
            backup.restore(target,BINDING,path,self.initial|{'revision':5},captured['archive_sha256'],'primary')
        with self.assertRaisesRegex(Rejected,'archive digest'):
            backup.restore(target,BINDING,path,self.initial,'a'*64,'primary')
        with self.assertRaisesRegex(Rejected,'pinned new quorum'):
            backup.restore(self.store,BINDING,path,self.initial,captured['archive_sha256'],'primary')
        self.assertFalse(target.values)
        target.change({'head':None},{'head':b'different authority'})
        with self.assertRaisesRegex(Rejected,'already holds'):
            backup.restore(target,BINDING,path,self.initial,captured['archive_sha256'],'primary')
        self.assertEqual(target.read('head')['head'].data,b'different authority')

    def test_inflight_backup_excludes_writer_credentials_and_refuses_activation(self):
        with closing(sqlite3.connect(self.checkpoint)) as db, db:
            db.execute("INSERT INTO actions VALUES (1,2,'enqueue','pending',NULL,0)")
        head=self.old.request('primary','/checkpoint',self.checkpoint.read_bytes(),7,4)
        self.store.change({'writer':None},{'writer':b'{"token":"fixture-sensitive-token","started":1}'})
        path=self.root/'inflight.lit-quorum'
        result=backup.backup(self.store,BINDING,path)
        self.assertEqual(result['recovery_blockers'],['actions','writer'])
        self.assertNotIn(b'fixture-sensitive-token',path.read_bytes())
        target=Store('3')
        with self.assertRaisesRegex(Rejected,'unresolved'):
            backup.restore(target,BINDING,path,head,result['archive_sha256'],'standby')
        self.assertFalse(target.values)


class GatewayClientTests(unittest.TestCase):
    def client(self):
        config={'url':'https://primary','alternates':['https://alternate'],
                'ca':'ca.crt','cert':'client.crt','key':'client.key'}
        with patch.object(replica.ssl,'create_default_context'):
            return replica.Client(config)

    def test_ambiguous_provider_response_does_not_replay_at_another_gateway(self):
        client=self.client()
        with patch.object(client,'opener') as opener:
            opener.open.side_effect=TimeoutError()
            with self.assertRaises(ReplicaUnavailable):
                client.call('/provider',{'operation':'installation'},epoch=2,revision=3)
        request,=opener.open.call_args.args
        self.assertEqual(request.full_url,'https://primary/provider')
        self.assertEqual(opener.open.call_count,1)

    def test_refused_provider_connection_uses_the_next_pinned_gateway(self):
        client=self.client()
        with patch.object(client,'opener') as opener:
            opener.open.side_effect=[URLError(ConnectionRefusedError()),io.BytesIO(b'{"total_count":1}')]
            self.assertEqual(client.call('/provider',{'operation':'installation'},epoch=2,revision=3),
                             {'total_count':1})
        requests=[call.args[0] for call in opener.open.call_args_list]
        self.assertEqual([r.full_url for r in requests],['https://primary/provider','https://alternate/provider'])
        self.assertEqual(requests[0].data,requests[1].data)

    def test_cluster_identity_is_checked_before_a_pinned_store_write(self):
        config={'urls':['https://one','https://two','https://three'],
                'ca':'ca.crt','cert':'client.crt','key':'client.key'}
        with patch.object(replica.ssl,'create_default_context'):
            store=EtcdStore(config,BINDING['repo'],cluster_id='6')
        with patch.object(store,'opener') as opener:
            opener.open.return_value=io.BytesIO(b'{"header":{"cluster_id":"7"}}')
            with self.assertRaises(ReplicaUnavailable):
                store.change({'head':None},{'head':b'must not be sent'})
        opener.open.assert_called_once()
        request,=opener.open.call_args.args
        self.assertEqual(json.loads(request.data),{'compare':[],'success':[]})


if __name__=='__main__':
    unittest.main()
