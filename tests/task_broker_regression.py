"""Native CLI/TLS/HTTP regression seam for the authenticated task broker."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from github_queue_coordinator_regression import Forge

CLI = Path(__file__).resolve().parents[1] / 'tools/task_broker.py'


class BrokerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cert_dir = tempfile.TemporaryDirectory(prefix='lit-broker-certs-')
        cls.pki = Path(cls.cert_dir.name)
        def openssl(*args):
            subprocess.run(['openssl', *args], cwd=cls.pki, check=True, capture_output=True)
        (cls.pki/'ca.cnf').write_text('[req]\ndistinguished_name=dn\nx509_extensions=ca\n[dn]\n[ca]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n')
        openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'ca.key',
                '-out', 'ca.crt', '-config', 'ca.cnf', '-subj', '/CN=Lit broker regression CA', '-days', '2')
        for name in ('server', 'admin', 'worker', 'other', 'unlisted', 'observer'):
            openssl('req', '-newkey', 'rsa:2048', '-nodes', '-keyout', name+'.key',
                    '-out', name+'.csr', '-subj', '/CN='+name)
            (cls.pki/'extensions').write_text('basicConstraints=critical,CA:FALSE\nauthorityKeyIdentifier=keyid,issuer\nsubjectKeyIdentifier=hash\nsubjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=' +
                                             ('serverAuth' if name == 'server' else 'clientAuth')+'\n')
            openssl('x509', '-req', '-in', name+'.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key',
                    '-CAcreateserial', '-out', name+'.crt', '-days', '2', '-extfile', 'extensions')
            (cls.pki/(name+'.key')).chmod(0o600)

    @classmethod
    def tearDownClass(cls):
        cls.cert_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lit-broker-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = {'number':7, 'head':'a'*40, 'branch':'agents/test/7', 'author':'worker[bot]'}
        self.forge = Forge(dict(self.spec))
        self.addCleanup(self.forge.close)
        self.config = {'state':str(self.root/'state'), 'listen':'127.0.0.1', 'port':0,
            'server_cert':str(self.pki/'server.crt'), 'server_key':str(self.pki/'server.key'),
            'client_ca':str(self.pki/'ca.crt'), 'poll_seconds':0.05, 'require_policy_observer':False,
            'manifest':{'repo':'100latent/lit-merge-queue-sandbox', 'main_ruleset':10,
                        'freeze_ruleset':11},
            'broker_app_id':99,'broker_author':'broker[bot]','publication_ruleset':13,
            'worker_installations':{'worker[bot]':{'app_id':1,'installation_id':11},
                                    'second[bot]':{'app_id':2,'installation_id':22}},
            'principals':{}}
        for name,role,owner in [('admin','scheduler',None),('worker','worker','worker[bot]'),
                                ('other','worker','second[bot]'),('observer','policy_observer',None)]:
            der=ssl.PEM_cert_to_DER_cert((self.pki/(name+'.crt')).read_text())
            self.config['principals'][hashlib.sha256(der).hexdigest()]={'role':role,'owner':owner}
        self.path=self.root/'config.json'
        self.token=self.root/'test-token';self.token.write_text('good');self.token.chmod(0o600)
        self.inherited_token=''
        self.reader=self.root/'reader-token';self.reader.write_text('fixture-read-only');self.reader.chmod(0o600)

    def start(self):
        self.path.write_text(json.dumps(self.config));self.path.chmod(0o600)
        self.log=(self.root/'service.log').open('a+')
        self.addCleanup(self.log.close)
        self.process=subprocess.Popen([sys.executable,str(CLI),'--config',str(self.path),
            '--ready-file',str(self.root/'ready.json'),'--test-api-url',self.forge.url,
            '--test-token-file',str(self.token),'--test-read-token-file',str(self.reader)],stdout=self.log,stderr=self.log,
            env=dict(os.environ,GH_TOKEN=self.inherited_token,GITHUB_TOKEN='',LIT_QUEUE_OBSERVER_TOKEN=''))
        self.addCleanup(self.stop)
        deadline=time.monotonic()+8
        while not (self.root/'ready.json').exists() and self.process.poll() is None and time.monotonic()<deadline:
            time.sleep(0.03)
        self.assertTrue((self.root/'ready.json').exists(),(self.root/'service.log').read_text())
        self.url='https://localhost:'+str(json.loads((self.root/'ready.json').read_text())['port'])

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate();self.process.wait(timeout=5)

    def call(self, name, path, payload=None, expected=200):
        context=ssl.create_default_context(cafile=str(self.pki/'ca.crt'))
        if name:
            context.load_cert_chain(str(self.pki/(name+'.crt')),str(self.pki/(name+'.key')))
        request=Request(self.url+path, None if payload is None else json.dumps(payload).encode(),
                        {'Content-Type':'application/json'})
        deadline=time.monotonic()+5
        while True:
            try:
                with urlopen(request,context=context,timeout=5) as response:
                    code=response.status;body=json.load(response)
            except HTTPError as error:
                code=error.code;body=json.load(error);error.close()
            # Public contract: busy enrollment is retried with identical bytes.
            # Negative/competing-claim cases still observe the first response.
            if (code==409 and expected==200 and path in ('/register','/propose','/recover-removed','/assign')
                    and body.get('error')=='rejected_or_busy' and time.monotonic()<deadline):
                time.sleep(.03)
                continue
            break
        self.assertIn(code,expected if isinstance(expected,tuple) else (expected,),body)
        return body

    def assign(self, generation=0, owner='worker[bot]', request=None):
        deadline=time.monotonic()+5
        while True:
            result=self.call('admin','/assign',{'task':'task-1','expect':generation,'owner':owner,
                'base':'c'*40,'request':request or str(generation)},expected=(200,409))
            if 'error' not in result:return result
            if time.monotonic()>deadline:self.fail('Assignment remained busy')
            time.sleep(.03)

    def register(self, claim, name='worker', expected=200):
        spec=self.spec|{k:claim[k] for k in ('task','generation','claim','base')}
        return self.call(name,'/register',{'proposal':spec,'capability':claim.get('capability','')},expected)

    def until(self, predicate):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            value=self.call('admin','/status')
            if predicate(value):return value
            time.sleep(.05)
        self.fail('Broker did not converge: '+json.dumps(value)+'\n'+(self.root/'service.log').read_text())

    def test_automatic_queue_retirement_and_successor_grant(self):
        self.start();first=self.assign();self.register(first)
        self.until(lambda s:s['proposals'] and s['proposals'][0]['phase']=='queued')
        pending=self.assign(1,owner='second[bot]')
        self.assertEqual(pending['phase'],'draining');self.assertNotIn('capability',pending)
        self.register(first,expected=409)
        self.until(lambda s:s['claims'][-1]['phase']=='active')
        next_claim=self.call('other','/claims')['claims'][0]
        self.assertIn('capability',next_claim)
        self.assertEqual(self.forge.mutations,['EnqueueProposal','WithdrawProposal','CloseProposal'])
        self.assertNotIn(first['capability'],(self.root/'service.log').read_text())

    def test_missing_revocation_observation_blocks_all_publication(self):
        self.config['require_policy_observer']=True
        self.forge.allow_observer=True
        self.start();claim=self.assign();self.register(claim)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if self.call('admin','/health')['last_error']:break
            time.sleep(.03)
        self.assertEqual(self.forge.mutations,[])
        self.assertIsNotNone(self.call('admin','/health')['last_error'])
        self.policy()
        self.until(lambda s:s['proposals'][0]['phase']=='queued')

    def test_unsuspended_worker_disables_broker_publication(self):
        self.config['require_policy_observer']=True;self.forge.allow_observer=True
        self.start();self.policy(suspended=False);self.register(self.assign())
        time.sleep(.25)
        self.assertEqual(self.forge.mutations,[])
        self.assertIsNotNone(self.call('admin','/health')['last_error'])
        self.policy();self.until(lambda s:s['proposals'][0]['phase']=='queued')

    def recover(self, request):
        deadline=time.monotonic()+5
        while True:
            result=self.call('admin','/recover-removed',request,expected=(200,409))
            if 'error' not in result:return result
            if time.monotonic()>deadline:self.fail('Recovery remained rejected or busy')
            time.sleep(.03)

    def test_operator_recovers_confirmed_native_queue_removal_once(self):
        self.start();self.register(self.assign())
        self.until(lambda s:s['proposals'][0]['phase']=='queued')
        self.forge.queued=False
        self.forge.removal_event={'__typename':'RemovedFromMergeQueueEvent','id':'removed-1',
            'reason':'branch_protection_failure','actor':{'__typename':'Bot','login':'github-merge-queue'}}
        self.until(lambda s:s['proposals'][0]['phase']=='awaiting_outcome')
        request={'number':7,'event':'removed-1'}
        self.call('worker','/recover-removed',request,expected=403)
        self.recover(request)
        self.until(lambda s:s['proposals'][0]['phase']=='queued')
        self.recover(request)
        self.assertEqual(self.forge.mutations.count('EnqueueProposal'),2)

    def test_operator_cannot_retry_a_genuinely_unknown_enqueue(self):
        self.forge.drop_without_apply=True
        self.start();self.register(self.assign())
        self.until(lambda s:s['proposals'][0]['phase']=='unknown_enqueue')
        self.forge.removal_event={'__typename':'RemovedFromMergeQueueEvent','id':'old-removal',
            'reason':'branch_protection_failure','actor':{'__typename':'Bot','login':'github-merge-queue'}}
        self.call('admin','/recover-removed',{'number':7,'event':'old-removal'},expected=409)
        self.assertEqual(self.forge.mutations,[])

    def test_missing_worker_revocation_proof_is_rejected(self):
        self.config['require_policy_observer']=True;self.forge.allow_observer=True
        self.start();self.policy(workers={},expected=409)
        self.register(self.assign());time.sleep(.2)
        self.assertEqual(self.forge.mutations,[])

    def test_restart_preserves_capabilities_and_pending_retirement(self):
        self.start();first=self.assign();self.register(first)
        self.until(lambda s:s['proposals'][0]['phase']=='queued')
        self.stop();(self.root/'ready.json').unlink()
        self.start()
        self.assertEqual(self.call('worker','/claims')['claims'][0]['claim'],first['claim'])
        self.assign(1,owner='second[bot]')
        self.until(lambda s:s['claims'][-1]['phase']=='active')
        self.assertEqual(self.forge.mutations.count('EnqueueProposal'),1)

    def test_scheduler_claims_are_scoped_and_stale_workers_are_fenced(self):
        self.start()
        first=self.assign()
        self.assertEqual(self.call('worker','/claims')['claims'],[first])
        self.assertEqual(self.call('other','/claims')['claims'],[])
        self.register(first,name='other',expected=403)
        next_claim=self.assign(1,owner='second[bot]')
        self.register(first,expected=409)
        self.assertNotIn('capability',self.call('worker','/claims')['claims'][0])
        self.assertEqual(self.call('other','/claims')['claims'],[next_claim])
        self.call('admin','/assign',{'task':'evil','expect':0,'owner':'unregistered[bot]',
            'base':'c'*40,'request':'unknown'},expected=400)

    def policy(self, name='observer', expected=200, at=None, suspended=True, workers=None):
        rules={}
        for number in (10,11,13):
            request=Request(self.forge.url+'/repos/100latent/lit-merge-queue-sandbox/rulesets/'+str(number),
                            headers={'Authorization':'Bearer observer'})
            with urlopen(request,timeout=5) as response:rules[str(number)]=json.load(response)
        return self.call(name,'/policy',{'observed_at':time.time() if at is None else at,'rules':rules,
            'workers':workers if workers is not None else {owner:row|{'suspended_at':'2026-11-18T00:00:00Z' if suspended else None} for owner,row in self.config['worker_installations'].items()}},expected)

    def test_expired_or_forged_policy_feed_fails_closed(self):
        self.config['require_policy_observer']=True;self.config['policy_ttl_seconds']=.5
        self.forge.allow_observer=True
        self.start();first=self.assign();self.register(first)
        time.sleep(.2);self.assertEqual(self.forge.mutations,[])
        self.policy('worker',expected=403)
        self.policy('admin',expected=403)
        self.policy(at=time.time()-60,expected=409)
        self.policy();self.until(lambda s:s['proposals'][0]['phase']=='queued')
        time.sleep(.6)
        self.assign(1,owner='second[bot]')
        time.sleep(.2)
        self.assertTrue(self.forge.queued)
        self.assertNotIn('capability',self.call('other','/claims')['claims'][0])
        self.policy();self.until(lambda s:s['claims'][-1]['phase']=='active')

    def test_live_policy_difference_cannot_use_cached_bypass_information(self):
        self.config['require_policy_observer']=True
        self.forge.allow_observer=True
        self.start();self.policy()
        self.forge.freeze=False
        self.register(self.assign())
        time.sleep(.25)
        self.assertEqual(self.forge.mutations,[])
        self.assertIsNotNone(self.call('admin','/health')['last_error'])

    def test_competing_authenticated_scheduler_claims_have_one_winner(self):
        self.start()
        def claim(number):
            return self.call('admin','/assign',{'task':'race','expect':0,'owner':'worker[bot]',
                'base':'c'*40,'request':'request-'+str(number)},expected=(200,409))
        with ThreadPoolExecutor(max_workers=12) as pool:results=list(pool.map(claim,range(12)))
        self.assertEqual(sum('claim' in result for result in results),1)
        self.assertEqual(len(self.call('worker','/claims')['claims']),1)

    def test_body_bounds_and_identity_spoofing_are_rejected(self):
        self.start();claim=self.assign()
        self.call('worker','/register',{'proposal':self.spec,'capability':'x'*65536},expected=400)
        self.call('worker','/register',{'proposal':self.spec,'capability':claim['capability'],
            'repo':'attacker/repository'},expected=409)
        self.register(claim,name='other',expected=403)
        self.assertEqual(self.forge.mutations,[])

    def test_removing_certificate_from_allowlist_revokes_it_after_restart(self):
        self.start();self.assign();self.stop();(self.root/'ready.json').unlink()
        der=ssl.PEM_cert_to_DER_cert((self.pki/'worker.crt').read_text())
        del self.config['principals'][hashlib.sha256(der).hexdigest()]
        self.start();self.call('worker','/claims',expected=403)

    def test_worker_proposes_through_broker_without_github_write_credentials(self):
        self.start();claim=self.assign()
        body={k:claim[k] for k in ('task','generation','claim','base','capability')}
        body.update(request='publication-1',files=[{'path':'src/example.txt','content':'new implementation\n'}])
        receipt=self.call('worker','/propose',body)
        self.assertEqual(receipt['author'],'broker[bot]')
        self.assertEqual(receipt['agent'],'worker[bot]')
        self.assertEqual(self.call('worker','/propose',body),receipt)
        self.call('worker','/register',{'proposal':receipt,'capability':claim['capability']})
        self.until(lambda s:s['proposals'][0]['phase']=='queued')

    def test_worker_identity_does_not_require_a_separate_github_app(self):
        for principal in self.config['principals'].values():
            if principal.get('owner')=='worker[bot]':principal['owner']='worker-unique-001'
        self.start();claim=self.assign(owner='worker-unique-001')
        self.assertEqual(self.call('worker','/claims')['claims'],[claim])
        self.assertEqual(self.call('other','/claims')['claims'],[])

    def test_publication_request_ids_are_scoped_to_the_attempt(self):
        self.start();first=self.assign()
        def body(claim):
            return {k:claim[k] for k in ('task','generation','claim','base','capability')}|{
                'request':'same-worker-local-id','files':[{'path':'src/a.txt','content':'change'}]}
        a=self.call('worker','/propose',body(first))
        second=self.call('admin','/assign',{'task':'another-task','expect':0,'owner':'second[bot]',
            'base':'c'*40,'request':'other-task-assignment'})
        b=self.call('other','/propose',body(second))
        self.assertNotEqual(a['claim'],b['claim'])
        self.assertNotEqual(a['number'],b['number'])

    def test_empty_broker_credential_never_borrows_inherited_writer_token(self):
        self.inherited_token='good';self.token.write_text('')
        self.start();self.register(self.assign());time.sleep(.25)
        self.assertEqual(self.forge.mutations,[])
        self.assertEqual(self.call('admin','/health')['last_error'],'ValueError')

    def test_workers_receive_only_repository_read_credentials(self):
        self.start()
        reader=self.call('worker','/repository-token')
        self.assertEqual(reader['token'],'fixture-read-only')
        self.assertEqual(reader['permissions'],{'contents':'read','pull_requests':'read'})
        self.call('admin','/repository-token',expected=403)
        self.call('unlisted','/repository-token',expected=403)

    def test_unknown_publication_is_not_replayed_or_admitted_by_stale_owner(self):
        self.start();claim=self.assign()
        body={k:claim[k] for k in ('task','generation','claim','base','capability')}
        body.update(request='publication-unknown',files=[{'path':'src/a.txt','content':'change'}])
        self.forge.drop_publication_reply=True
        self.call('worker','/propose',body,expected=409)
        calls=list(self.forge.publication_calls)
        self.call('worker','/propose',body,expected=409)
        self.assertEqual(self.forge.publication_calls,calls)
        self.assign(1,owner='second[bot]')
        self.call('worker','/propose',body,expected=409)
        self.assertEqual(self.forge.mutations,[])
        self.assertEqual(self.call('admin','/status')['proposals'],[])

    def test_publisher_rejects_path_and_workflow_escape(self):
        self.start();claim=self.assign()
        body={k:claim[k] for k in ('task','generation','claim','base','capability')}
        body.update(request='publication-path')
        for path in ('../escape','/absolute','.GiT/config','.github/workflows/inject.yml','a\\b'):
            self.call('worker','/propose',body|{'files':[{'path':path,'content':'change'}]},expected=409)
        self.assertEqual(self.forge.publication_calls,[])

    def test_mtls_and_role_boundaries(self):
        self.start()
        with self.assertRaises((URLError, ssl.SSLError, ConnectionError)):
            self.call(None,'/claims')
        self.call('unlisted','/claims',expected=403)
        self.call('worker','/assign',{},expected=403)
        self.call('worker','/status',expected=403)
        self.assertEqual(self.call('worker','/claims')['claims'],[])


if __name__ == '__main__':
    unittest.main()
