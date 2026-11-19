"""Independent durable authority and fencing contract tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import broker_admission as admission
import github_queue_coordinator as queue
import task_broker_failover as failover
import task_broker_replica as replica

BINDING = {'repo':'100latent/lit-merge-queue-sandbox','main_ruleset':10,'freeze_ruleset':11}


class Provider:
    def __init__(self):
        self.calls = []
        self.sent = threading.Event()
        self.release = threading.Event()

    def mutate(self, kind, node, head):
        self.calls.append((kind,node,head))
        self.sent.set()
        if not self.release.wait(5):
            raise RuntimeError('fixture provider did not respond')
        return {'observed':True}

    def api(self, path, payload=None, writer=False):
        self.calls.append((path,payload,writer))
        return {'ok':True}


class ReplicaTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root/'replica').mkdir()
        self.provider = Provider()
        self.authority = replica.Authority(self.root/'replica',BINDING,lambda:self.provider,
                                           lambda:('read-only-fixture',100),lambda:('broker-read-only',100))
        with queue.database(self.root/'broker') as db, db:
            db.execute('CREATE TABLE publications(claim TEXT PRIMARY KEY, request TEXT, digest TEXT, phase TEXT, receipt TEXT)')
            admission.initialize(db)
            db.execute("INSERT INTO metadata VALUES ('manifest',?)",(queue.encoded(BINDING),))
        self.path = self.root/'broker'/'queue.sqlite'
        self.original = self.path.read_bytes()
        self.authority.request('manager','/bootstrap',b'{"holder":"primary"}\n'+self.original,0,-1)

    def request(self, actor, route, payload=b'', epoch=1, revision=0):
        if isinstance(payload,dict):
            payload = queue.encoded(payload).encode()
        return self.authority.request(actor,route,payload,epoch,revision)

    def change(self):
        return {'from':'primary','to':'standby','epoch':1,'request':'a'*32}

    def test_committed_snapshot_survives_replica_restart_and_response_retry(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO metadata VALUES ('marker','durable')")
        newer = self.path.read_bytes()
        first = self.request('primary','/checkpoint',newer)
        self.assertEqual(first['revision'],1)
        self.assertEqual(self.request('primary','/checkpoint',newer), first)
        with self.assertRaises(replica.Rejected):
            self.request('primary','/checkpoint',self.original)
        self.authority=replica.Authority(self.root/'replica',BINDING,lambda:self.provider,
                                         lambda:('reader',100),lambda:('broker-read-only',100))
        self.assertEqual(self.request('standby','/head'),first)
        self.assertEqual(self.request('primary','/snapshot',revision=1),newer)

    def test_promotion_serializes_against_an_inflight_provider_request(self):
        outgoing={'operation':'queue','kind':'enqueue','node':'PR_1','head':'a'*40}
        with ThreadPoolExecutor(max_workers=2) as pool:
            sending=pool.submit(self.request,'primary','/provider',outgoing)
            self.assertTrue(self.provider.sent.wait(3))
            entering=threading.Event()
            def promote():
                entering.set()
                return self.request('manager','/promote',self.change())
            switch=pool.submit(promote)
            self.assertTrue(entering.wait(3))
            self.assertFalse(switch.done())
            self.provider.release.set()
            self.assertEqual(sending.result(timeout=3),{'observed':True})
            moved=switch.result(timeout=3)
        self.assertEqual(moved,self.request('manager','/promote',self.change()))
        self.assertEqual(self.request('standby','/snapshot',epoch=2),self.original)
        with self.assertRaises(replica.Rejected):
            self.request('primary','/provider',outgoing)
        with self.assertRaises(replica.Rejected):
            self.request('primary','/checkpoint',self.original)
        with self.assertRaises(replica.Rejected):
            self.request('primary','/provider',outgoing,epoch=2)
        self.assertEqual(len(self.provider.calls),1)

    def test_provider_is_pinned_to_broker_namespace_and_no_writer_tokens_are_returned(self):
        for path,payload in (
            ('repos/other/repo/git/trees',{}),('repos/'+BINDING['repo']+'/statuses/a',{}),
            ('repos/'+BINDING['repo']+'/git/refs',{'ref':'refs/heads/main'}),
            ('repos/'+BINDING['repo']+'/pulls',{'base':'main','head':'foreign'}),
        ):
            with self.subTest(path=path), self.assertRaises(replica.Rejected):
                self.request('primary','/provider',{'operation':'publish','path':path,'payload':payload})
        self.assertEqual(self.provider.calls,[])
        self.assertEqual(self.request('primary','/reader')['token'],'read-only-fixture')
        self.assertEqual(self.request('primary','/broker-reader')['token'],'broker-read-only')
        self.assertTrue(all(value=='read' for value in replica.BROKER_READ.values()))
        with self.assertRaises(replica.Rejected):
            self.request('standby','/reader')

    def test_watch_transfer_uses_independent_fence_and_can_resume(self):
        calls=[]
        def standby(action):
            calls.append(action)
            if action in ('ready','identity'):
                return {'binding':BINDING,'machine':'standby','replica_actor':'standby',
                        'replica_url':'https://replica'}
            self.assertEqual(self.request('primary','/head')['holder'],'standby')
        class Manager:
            url='https://replica'
            call=lambda inner,route,data=b'':self.request('manager',route,data)
        with redirect_stdout(io.StringIO()):
            journal=self.root/'handoff.json'
            first=failover.transfer_replica(Manager(),standby,journal)
            self.assertEqual(first,failover.transfer_replica(Manager(),standby,journal))
            failback={'from':'standby','to':'primary','epoch':2,'request':'b'*32}
            self.request('manager','/promote',failback)
            second=failover.transfer_replica(Manager(),standby,self.root/'handoff-epoch-3.json')
        self.assertEqual(first['sha256'],hashlib.sha256(self.original).hexdigest())
        self.assertEqual(second['sha256'],first['sha256'])
        self.assertNotEqual(second['handoff'],first['handoff'])
        self.assertEqual(self.request('manager','/head')['epoch'],4)
        self.assertEqual(calls,['ready','identity','start-replica','identity','start-replica',
                                'ready','identity','start-replica'])

    def test_replicated_standby_accepts_only_its_verified_previous_cache(self):
        host=failover.SystemdHost('fixture.service',{'state':str(self.path.parent),'manifest':BINDING,
            'replica':{'actor':'standby','url':'https://replica'}})
        host.marker=self.root/'fence'
        host.restored=self.root/'restored'
        self.path.chmod(0o600)
        with patch.object(host,'stopped'), patch.object(host,'identity',return_value={'replica_actor':'standby'}):
            with self.assertRaises(FileNotFoundError):
                host.ready()
            (self.path.parent/'replicated-authority').touch(mode=0o600)
            self.assertEqual(host.ready(),{'replica_actor':'standby'})
            sidecar=self.path.with_name('queue.sqlite-wal')
            sidecar.touch()
            with self.assertRaisesRegex(RuntimeError,'sidecar'):
                host.ready()
            sidecar.unlink()
            host.binding=BINDING | {'repo':'other/repository'}
            with self.assertRaisesRegex(ValueError,'binding differs'):
                host.ready()
            host.replica=None
            with self.assertRaisesRegex(RuntimeError,'already bound'):
                host.ready()

    def test_watcher_restart_during_outage_reuses_only_the_armed_epoch(self):
        class Manager:
            url='https://replica'
            call=lambda inner,route,data=b'':self.request('manager',route,data)
        authority=Manager()
        config={'ca':'ca.crt','cert':'client.crt','key':'client.key','replica':{},
                'poll_seconds':.01,'failure_threshold':2,
                'primary':{'url':'https://primary'},'standby':{'url':'https://standby'}}
        journal=self.root/'handoff.json'
        armed=self.root/'handoff.json.armed'
        target={'binding':BINDING,'machine':'standby','replica_actor':'standby','replica_url':authority.url}
        online={'last_error':None,'last_cycle':failover.time.time()}
        def standby(action):
            if action in ('ready','identity'):
                return target
            self.assertEqual(authority.call('/head')['holder'],'standby')

        with (patch.object(failover.ssl,'create_default_context'), patch.object(replica,'Client',return_value=authority),
              patch.object(failover,'Remote',return_value=standby), redirect_stdout(io.StringIO())):
            with patch.object(failover,'reachable',return_value=False):
                with self.assertRaisesRegex(RuntimeError,'authenticated primary baseline'):
                    failover.watch(config,journal)
            self.assertFalse(armed.exists())
            with patch.object(failover,'reachable',side_effect=[online,KeyboardInterrupt()]):
                with self.assertRaises(KeyboardInterrupt):
                    failover.watch(config,journal)
            self.assertFalse(journal.exists())
            self.assertEqual(armed.stat().st_mode & 0o777,0o600)
            with patch.object(failover,'reachable',side_effect=[False,False,online]):
                failover.watch(config,journal)
            self.assertEqual(authority.call('/head')['epoch'],2)
            self.request('manager','/promote',{'from':'standby','to':'primary','epoch':2,'request':'b'*32})
            saved=self.root/'stale-epoch.json'
            armed.rename(saved.with_name(saved.name+'.armed'))
            with patch.object(failover,'reachable',return_value=False) as probe:
                with self.assertRaisesRegex(RuntimeError,'binding or epoch differs'):
                    failover.watch(config,saved)
                probe.assert_not_called()
            with self.assertRaisesRegex(RuntimeError,'advanced beyond the armed epoch'):
                failover.transfer_replica(authority,standby,saved,expected_epoch=1)
            self.assertEqual(authority.call('/head')['epoch'],3)
            self.assertFalse(saved.exists())


if __name__=='__main__':
    unittest.main()
