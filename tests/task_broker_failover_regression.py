"""Test the unattended handoff journal and refusal paths."""
import hashlib
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import task_broker_failover as failover
import task_broker_client as client
import task_broker_policy_observer as observer


class Peer:
    def __init__(self,name,events):
        self.name=name
        self.events=events
        self.binding={'repo':'100latent/lit-merge-queue-sandbox','main_ruleset':1,'freeze_ruleset':2}
        self.payload=b'current authority checkpoint'
        self.handoff=None
        self.fail=None
        self.restored=None
        self.starts=0

    def __call__(self,action,*args,data=None):
        self.events.append((self.name,action))
        if self.fail==action:
            raise RuntimeError('injected management failure')
        if action in ('identity','ready'):
            return {'machine':self.name,'binding':self.binding}
        if action=='fence':
            self.handoff=args[0]
        elif action in ('verify-fence','checkpoint'):
            if args[0]!=self.handoff:
                raise RuntimeError('no source fence')
            if action=='checkpoint':
                return self.payload
        elif action=='restore':
            assert args[1]==hashlib.sha256(data).hexdigest()
            if self.restored is not None:
                assert self.restored==(args,data)
            self.restored=(args,data)
        elif action=='activate':
            assert self.restored[0]==args
            if not self.starts:
                self.starts+=1
        return {}


class FailoverTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.journal=Path(tmp.name)/'handoff.json'
        self.checkpoint=self.journal.with_name(self.journal.name+'.sqlite')
        self.events=[]
        self.primary=Peer('primary',self.events)
        self.standby=Peer('standby',self.events)

    def transfer(self):
        return failover.transfer(self.primary,self.standby,self.journal)

    def test_promotes_only_after_fence_and_exact_restore(self):
        result=self.transfer()
        self.assertEqual(result['stage'],'promoted')
        self.assertEqual(self.standby.restored[1],self.primary.payload)
        self.assertEqual(self.events[-6:],[('primary','fence'),('primary','checkpoint'),
            ('primary','verify-fence'),('standby','restore'),('primary','verify-fence'),
            ('standby','activate')])
        self.assertFalse(self.checkpoint.exists())
        self.assertEqual(self.transfer(),result)
        self.assertEqual(self.standby.starts,1)

    def test_unreachable_source_does_not_restore_or_promote(self):
        for stage in ('identity','offline','fence','checkpoint','verify-fence'):
            with self.subTest(stage=stage):
                self.journal.unlink(missing_ok=True)
                self.checkpoint.unlink(missing_ok=True)
                self.primary.fail=stage
                with self.assertRaises(RuntimeError):
                    self.transfer()
                self.assertIsNone(self.standby.restored)
                self.assertEqual(self.standby.starts,0)

    def test_resume_after_failed_restore_uses_exact_saved_checkpoint(self):
        self.standby.fail='restore'
        with self.assertRaises(RuntimeError):
            self.transfer()
        self.assertEqual(json.loads(self.journal.read_text())['stage'],'restore')
        initial=self.checkpoint.read_bytes()
        self.assertEqual(self.checkpoint.stat().st_mode & 0o777,0o600)
        self.primary.payload=b'only the originally fenced checkpoint can be restored'
        self.standby.fail=None
        self.transfer()
        self.assertEqual(self.standby.restored[1],initial)
        self.assertEqual(self.events.count(('primary','checkpoint')),1)
        self.assertFalse(self.checkpoint.exists())

    def test_resume_during_activation_reconfirms_fence_and_does_not_copy_again(self):
        self.standby.fail='activate'
        with self.assertRaises(RuntimeError):
            self.transfer()
        self.assertEqual(json.loads(self.journal.read_text())['stage'],'activate')
        self.primary.fail='verify-fence'
        self.standby.fail=None
        with self.assertRaises(RuntimeError):
            self.transfer()
        self.assertEqual(self.standby.starts,0)
        self.primary.fail=None
        self.transfer()
        self.assertEqual(self.events.count(('standby','restore')),1)
        self.assertEqual(self.standby.starts,1)

    def test_refuses_different_policy_or_same_machine(self):
        self.standby.binding=dict(self.standby.binding,freeze_ruleset=9)
        with self.assertRaises(RuntimeError):
            self.transfer()
        self.standby.binding=self.primary.binding
        self.standby.name=self.primary.name
        with self.assertRaises(RuntimeError):
            self.transfer()
        self.assertNotIn(('primary','fence'),self.events)
        self.assertFalse(self.journal.exists())

    def test_checkpoint_without_matching_journal_cannot_be_reused(self):
        self.checkpoint.write_bytes(b'stale checkpoint')
        with self.assertRaisesRegex(RuntimeError,'no matching handoff journal'):
            self.transfer()
        self.assertEqual(self.events,[])
        self.assertFalse(self.journal.exists())

    def test_watch_requires_baseline_and_consecutive_failures(self):
        config={'ca':'ca.crt','cert':'client.crt','key':'client.key','poll_seconds':.01,'failure_threshold':2,
                'primary':{'url':'https://primary'},'standby':{'url':'https://standby'}}
        with patch.object(failover.ssl,'create_default_context'), patch.object(failover,'Remote',side_effect=(self.primary,self.standby)):
            with patch.object(failover,'reachable',return_value=False):
                with self.assertRaisesRegex(RuntimeError,'authenticated primary baseline'):
                    failover.watch(config,self.journal)
        with patch.object(failover.ssl,'create_default_context'), patch.object(failover,'Remote',side_effect=(self.primary,self.standby)):
            active={'last_error':None,'last_cycle':failover.time.time()}
            with patch.object(failover,'reachable',side_effect=(active,False,active,False,False,active)) as probe:
                failover.watch(config,self.journal)
        self.assertEqual(probe.call_count,6)
        self.assertEqual(self.standby.starts,1)

    def test_connection_eof_is_a_miss_but_certificate_failure_is_rejected(self):
        with patch.object(failover,'build_opener') as opener:
            opener.return_value.open.side_effect=URLError(failover.ssl.SSLEOFError(8,'socket closed'))
            self.assertFalse(failover.reachable('https://primary',None))
            opener.return_value.open.side_effect=URLError(failover.ssl.SSLCertVerificationError(1,'untrusted'))
            with self.assertRaisesRegex(RuntimeError,'TLS verification failed'):
                failover.reachable('https://primary',None)

    def test_client_resumes_accepted_read_on_standby_without_reposting(self):
        claim='d'*32
        path='/publication?claim='+claim
        def response(body,status):
            stream=io.BytesIO(json.dumps(body).encode())
            stream.status=status
            stream.headers={'Retry-After':'.01'}
            return stream
        urls=['https://primary','https://standby']
        argv=['client','--url',urls[0],'--standby-url',urls[1],
              '--cert','client.crt','--ca','ca.crt','--key','client.key','propose']
        unavailable=HTTPError(urls[0]+path,503,'Fenced',{'Retry-After':'.01'},
                              io.BytesIO(b'{"error":"authority_unavailable","retryable":true}'))
        for failure in (URLError(failover.ssl.SSLEOFError(8,'socket closed')),unavailable):
            calls=[response({'operation':claim,'status_path':path},202),failure,
                   response({'number':325,'claim':claim},200)]
            stdin=SimpleNamespace(buffer=io.BytesIO(b'{"probe":"write"}'))
            with (self.subTest(failure=type(failure).__name__),
                  patch.object(client.sys,'argv',argv), patch.object(client.sys,'stdin',stdin),
                  patch.object(client.ssl,'create_default_context'), patch.object(client,'build_opener') as opener,
                  redirect_stdout(io.StringIO()) as out):
                opener.return_value.open.side_effect=calls
                self.assertEqual(client.main(),0)
            self.assertEqual(json.loads(out.getvalue()),{'number':325,'claim':claim})
            requests=[call.args[0] for call in opener.return_value.open.call_args_list]
            self.assertEqual([(r.get_method(),r.full_url) for r in requests],
                             [('POST',urls[0]+'/propose'),('GET',urls[0]+path),('GET',urls[1]+path)])

    def test_client_does_not_replay_a_post_when_authority_is_unavailable(self):
        argv=['client','--url','https://primary','--standby-url','https://standby',
              '--cert','client.crt','--ca','ca.crt','--key','client.key','--no-wait','propose']
        body=b'{"error":"authority_unavailable","retryable":true}'
        stdin=SimpleNamespace(buffer=io.BytesIO(b'{"probe":"write"}'))
        with (patch.object(client.sys,'argv',argv), patch.object(client.sys,'stdin',stdin),
              patch.object(client.ssl,'create_default_context'), patch.object(client,'build_opener') as opener,
              redirect_stdout(io.StringIO()) as out):
            opener.return_value.open.side_effect=HTTPError('https://primary/propose',503,'Fenced',
                                                           {},io.BytesIO(body))
            self.assertEqual(client.main(),1)
        self.assertEqual(json.loads(out.getvalue()),json.loads(body))
        request=opener.return_value.open.call_args.args[0]
        self.assertEqual((request.get_method(),request.full_url),('POST','https://primary/propose'))
        self.assertEqual(opener.return_value.open.call_count,1)

    def test_client_fallback_distinguishes_connect_failure_from_uncertain_write(self):
        self.assertTrue(client.can_use_standby(URLError(ConnectionRefusedError()),False))
        self.assertFalse(client.can_use_standby(ConnectionResetError(),False))
        self.assertTrue(client.can_use_standby(ConnectionResetError(),True))
        self.assertFalse(client.can_use_standby(URLError(failover.ssl.SSLCertVerificationError()),True))

    def test_observer_posts_to_reachable_standby_when_primary_is_down(self):
        urls=['https://primary','https://standby']
        payload=b'{"observed_at":1}'
        with patch.object(observer,'post_observation',side_effect=lambda url,*_:url==urls[1]) as post:
            self.assertEqual(observer.deliver(urls,None,payload),1)
        self.assertEqual({call.args for call in post.call_args_list},{(url,None,payload) for url in urls})


if __name__=='__main__':
    unittest.main()
