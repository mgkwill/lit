"""Native CLI/TLS/HTTP regression seam for the authenticated task broker."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
import signal
from pathlib import Path
import sqlite3
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
CLIENT = CLI.with_name('task_broker_client.py')
CHECKPOINT = CLI.with_name('task_broker_checkpoint.py')
RENEW = CLI.with_name('task_broker_renew_client.py')


class GitDataFixture:
    """Back the Forge's Git data endpoints with Git objects for byte-level checks."""

    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.git('init', '--object-format=sha1')
        self.git('config', 'user.name', 'Broker regression')
        self.git('config', 'user.email', 'broker@example.invalid')
        (root/'nested').mkdir()
        (root/'nested'/'remove').write_text('remove me\n')
        (root/'nested'/'mode-only').write_bytes(b'\x00\xffexisting\n')
        (root/'keep').write_text('keep me\n')
        (root/'link').symlink_to('keep')
        self.git('add', '--all')
        self.git('commit', '-m', 'Assignment base')
        self.base = self.git('rev-parse', 'HEAD').decode().strip()

    def git(self, *args, data=None):
        return subprocess.run(['git', '-C', str(self.root), *args], input=data,
                              check=True, capture_output=True).stdout

    def entries(self, tree, *args):
        result = []
        for record in self.git('ls-tree', '-z', *args, tree).decode().split('\0')[:-1]:
            meta, path = record.split('\t', 1)
            mode, kind, sha = meta.split()
            result.append({'path':path, 'mode':mode, 'type':kind, 'sha':sha})
        return result

    def blob(self, request):
        if request['encoding'] != 'base64':
            raise ValueError('expected a base64 blob')
        return self.git('hash-object', '-w', '--stdin',
                        data=base64.b64decode(request['content'], validate=True)).decode().strip()

    def tree(self, request):
        self.git('read-tree', request['base_tree'])
        for entry in request['tree']:
            path = entry['path']
            if 'content' in entry:
                sha = self.git('hash-object', '-w', '--stdin', data=entry['content'].encode()).decode().strip()
            else:
                sha = entry['sha']
            if sha is None:
                self.git('update-index', '--force-remove', '--', path)
            else:
                self.git('update-index', '--add', '--cacheinfo', ','.join((entry['mode'], sha, path)))
        return self.git('write-tree').decode().strip()

    def commit(self, request):
        parent, = request['parents']
        return self.git('commit-tree', request['tree'], '-p', parent, '-m', request['message']).decode().strip()


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
        self.process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('run_with_trace.py')),str(CLI),'--config',str(self.path),
            '--ready-file',str(self.root/'ready.json'),'--test-api-url',self.forge.url,
            '--test-token-file',str(self.token),'--test-read-token-file',str(self.reader)],stdout=self.log,stderr=self.log,
            env=dict(os.environ,GH_TOKEN=self.inherited_token,GITHUB_TOKEN='',LIT_QUEUE_OBSERVER_TOKEN=''))
        self.addCleanup(self.stop)
        deadline=time.monotonic()+8
        while not (self.root/'ready.json').exists() and self.process.poll() is None and time.monotonic()<deadline:
            time.sleep(0.03)
        if not (self.root/'ready.json').exists() and self.process.poll() is None:
            try:
                self.process.send_signal(signal.SIGUSR1)
                time.sleep(0.2)
            except ProcessLookupError:
                pass
        self.assertTrue((self.root/'ready.json').exists(),
                        f'broker exit={self.process.poll()}\n' + (self.root/'service.log').read_text())
        self.url='https://localhost:'+str(json.loads((self.root/'ready.json').read_text())['port'])

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate();self.process.wait(timeout=5)

    def checkpoint(self, config, *args):
        return subprocess.run([sys.executable,str(CHECKPOINT),'--config',str(config),*args],
                              capture_output=True,timeout=8)

    def renew_worker(self, new, output, phase):
        return subprocess.run([sys.executable,str(RENEW),'--config',str(self.path),
            '--old-cert',str(self.pki/'worker.crt'),'--new-cert',str(self.pki/(new+'.crt')),
            '--output',str(output),phase],capture_output=True,timeout=8)

    def call(self, name, path, payload=None, expected=200):
        context=ssl.create_default_context(cafile=str(self.pki/'ca.crt'))
        if name:
            context.load_cert_chain(str(self.pki/(name+'.crt')),str(self.pki/(name+'.key')))
        request=Request(self.url+path, None if payload is None else json.dumps(payload).encode(),
                        {'Content-Type':'application/json'})
        deadline=time.monotonic()+5
        accepted_codes=expected if isinstance(expected,tuple) else (expected,)
        while True:
            try:
                with urlopen(request,context=context,timeout=5) as response:
                    code=response.status;body=json.load(response)
            except HTTPError as error:
                code=error.code;body=json.load(error);error.close()
            if code==202 and 202 not in accepted_codes:
                self.assertLess(time.monotonic(),deadline,'publication stayed pending')
                request=Request(self.url+body['status_path'])
                time.sleep(.03)
                continue
            # Retry only explicitly transient responses with identical bytes.
            # Competing claims include 503 and retain their first response.
            if (code in (429,503) and code not in accepted_codes
                    and body.get('retryable') and time.monotonic()<deadline):
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
                'base':'c'*40,'request':'request-'+str(number)},expected=(200,409,503))
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

    def test_mixed_file_publication_produces_expected_git_commit(self):
        git = self.forge.git_data = GitDataFixture(self.root/'git')
        self.start()
        data = bytes(range(256))
        body = self.publication('mixed-files', base=git.base)
        body['files'] = [
            {'path':'nested/asset.bin','content':base64.b64encode(data).decode(),'encoding':'base64'},
            {'path':'nested/remove','delete':True},
            {'path':'nested/mode-only','mode':'100755'},
            {'path':'bin/run','content':'#!/bin/sh\necho café\n','mode':'100755','encoding':'utf-8'},
        ]
        receipt = self.call('worker','/propose',body)
        head = receipt['head']
        self.assertEqual(git.git('rev-parse','refs/heads/'+receipt['branch']).decode().strip(),head)
        self.assertEqual(git.git('rev-parse',head+'^').decode().strip(),git.base)
        entries = {entry['path']:entry for entry in git.entries(head, '-r')}
        self.assertEqual({path:entry['mode'] for path,entry in entries.items()}, {
            'nested/asset.bin':'100644','nested/mode-only':'100755','bin/run':'100755',
            'keep':'100644','link':'120000',
        })
        self.assertEqual(git.git('cat-file','blob',head+':nested/asset.bin'),data)
        self.assertEqual(git.git('cat-file','blob',head+':bin/run'),'#!/bin/sh\necho café\n'.encode())
        for path in ('nested/mode-only','keep','link'):
            self.assertEqual(git.git('rev-parse',head+':'+path),git.git('rev-parse',git.base+':'+path))
        calls = self.forge.publication_calls[:]
        self.assertEqual(len(calls),5)
        self.assertEqual(self.call('worker','/propose',body),receipt)
        changed = body | {'files':body['files'][:2]+[{'path':'nested/mode-only','mode':'100644'}]+body['files'][3:]}
        rejected = self.call('worker','/propose',changed,expected=409)
        self.assertEqual(rejected['error'],'publication_request_conflict')
        self.assertEqual(self.forge.publication_calls,calls)

    def test_publication_prechecks_every_edit_before_blob_upload(self):
        git = self.forge.git_data = GitDataFixture(self.root/'git')
        self.start()
        cases = [
            ({'path':'missing','delete':True},'publication_path_not_found'),
            ({'path':'missing/mode','mode':'100755'},'publication_path_not_found'),
            ({'path':'nested','content':'replace directory'},'publication_path_not_regular'),
            ({'path':'keep/child','content':'replace parent'},'publication_path_not_regular'),
            ({'path':'link','delete':True},'publication_path_not_regular'),
        ]
        for i,(file,code) in enumerate(cases):
            with self.subTest(file=file):
                body = self.publication(f'invalid-base-edit-{i}',base=git.base)
                body['files'] = [{'path':'binary','content':'/w==','encoding':'base64'},file]
                rejected = self.call('worker','/propose',body,expected=409)
                self.assertEqual(rejected['error'],code)
        self.assertEqual(self.forge.publication_calls,[])

    def test_truncated_base_tree_blocks_publication(self):
        self.forge.truncate_base_tree = True
        self.start()
        body = self.publication('truncated-tree')
        self.call('worker','/propose',body,expected=202)
        self.until(lambda s:s['publication_jobs'][0]['error']=='publication_precondition_unavailable')
        self.assertEqual(self.forge.publication_calls,[])

    def test_lost_blob_response_is_unknown_and_never_replayed(self):
        git = self.forge.git_data = GitDataFixture(self.root/'git')
        self.forge.drop_blob_reply = True
        self.start()
        body = self.publication('lost-blob-response',base=git.base)
        body['files'] = [{'path':'binary','content':'/w==','encoding':'base64'}]
        result = self.call('worker','/propose',body,expected=409)
        self.assertEqual(result,{'error':'publication_outcome_unknown','retryable':False})
        missing=self.call('admin','/reconcile-publication',{'claim':body['claim']},expected=409)
        self.assertEqual(missing['error'],'publication_head_unrecorded')
        self.assertEqual(self.call('worker','/propose',body,expected=409),result)
        self.assertEqual(self.forge.publication_calls,['/repos/100latent/lit-merge-queue-sandbox/git/blobs'])

    def test_publication_is_accepted_durably_before_remote_work_completes(self):
        self.forge.block_publication = True
        self.start();claim=self.assign()
        body={k:claim[k] for k in ('task','generation','claim','base','capability')}
        body.update(request='accepted-publication',files=[{'path':'src/a.txt','content':'change'}])
        accepted=self.call('worker','/propose',body,expected=202)
        self.assertIn(accepted['state'],('queued','running'))
        self.assertFalse(accepted['retryable'])
        self.assertTrue(self.forge.publication_started.wait(3))
        pending=self.call('worker',accepted['status_path'],expected=202)
        self.assertEqual(pending['state'],'running')
        self.forge.publication_release.set()

    def publication(self, task, owner='worker[bot]', base='c'*40):
        claim=self.call('admin','/assign',{'task':task,'expect':0,'owner':owner,
            'base':base,'request':'assign-'+task})
        return {k:claim[k] for k in ('task','generation','claim','base','capability')}|{
            'request':'publish-'+task,'files':[{'path':task+'.txt','content':task}]}

    def dependency_assignment(self, task, base, expected=200, request=None):
        return self.call('admin','/assign',{'task':task,'expect':0,'owner':'worker[bot]',
            'base':base,'request':request or 'dependency-assign-'+task},expected=expected)

    def prerequisite_integration(self, claim, merge, number=7):
        # The coordinator's Git/Actions receipt verification has independent
        # coverage. This fixture represents its durable output to scheduling.
        proposal={k:claim[k] for k in ('task','base','generation','claim')} | self.spec | {'number':number}
        receipt={'number':number,'proposal':proposal,'merge_commit':merge}
        with closing(sqlite3.connect(self.root/'state'/'queue.sqlite')) as db, db:
            db.execute('UPDATE claims SET proposal=? WHERE claim=?',(number,claim['claim']))
            db.execute("INSERT OR IGNORE INTO proposals(number,spec,phase) VALUES (?,?,'merged')",
                       (number,json.dumps(proposal)))
            if merge:
                db.execute('INSERT OR REPLACE INTO integrations(number,receipt,retry_after) VALUES (?,?,0)',
                           (number,json.dumps(receipt)))

    def test_durable_scheduler_plan_requires_exact_verified_merges(self):
        self.start()
        upstream=self.dependency_assignment('upstream','c'*40)
        plan={'task':'downstream','requires':['upstream']}
        self.call('worker','/dependencies',plan,expected=403)
        expected={'task':'downstream','requires':['upstream'],'blocked':['upstream'],'verified':[]}
        self.assertEqual(self.call('admin','/dependencies',plan),expected)
        self.prerequisite_integration(upstream,None)
        self.stop();(self.root/'ready.json').unlink();self.start()
        self.assertEqual(self.call('admin','/status')['dependency_plans'],[expected])
        denied=self.dependency_assignment('downstream','d'*40,expected=409)
        self.assertEqual(denied,{'error':'prerequisite_merge_unverified','prerequisites':['upstream'],'retryable':False})
        self.assertFalse(any(c['task']=='downstream' for c in self.call('admin','/status')['claims']))
        self.prerequisite_integration(upstream,'d'*40)
        denied=self.dependency_assignment('downstream','e'*40,expected=409)
        self.assertEqual(denied['error'],'dependency_base_unverified')
        self.forge.compare_error=503
        denied=self.dependency_assignment('downstream','f'*40,expected=503)
        self.assertEqual(denied.get('error'),'dependency_proof_unavailable')
        grant=self.dependency_assignment('downstream','d'*40)
        self.assertEqual(grant['phase'],'active')

    def test_dependency_base_accepts_descendant_and_replays_original_grant(self):
        self.start()
        upstream=self.dependency_assignment('upstream','c'*40)
        self.prerequisite_integration(upstream,'d'*40)
        self.call('admin','/dependencies',{'task':'downstream','requires':['upstream']})
        self.call('admin','/dependencies',{'task':'late','requires':['upstream']})
        self.forge.compare_results[('d'*40,'e'*40)]='ahead'
        grant=self.dependency_assignment('downstream','e'*40)
        self.assertEqual(grant['phase'],'active')
        self.assertEqual(self.forge.compare_requests,[('d'*40,'e'*40)])
        with closing(sqlite3.connect(self.root/'state'/'queue.sqlite')) as db:
            event=json.loads(db.execute("SELECT data FROM events WHERE kind='task_assignment' ORDER BY id DESC").fetchone()[0])
        self.assertEqual(event['prerequisite_merges'],[
            {'task':'upstream','generation':1,'number':7,'merge_commit':'d'*40}])
        self.call('admin','/assign',{'task':'upstream','expect':1,'owner':'second[bot]',
            'base':'e'*40,'request':'reassign-upstream'})
        blocked=self.dependency_assignment('late','d'*40,expected=409)
        self.assertEqual(blocked['error'],'prerequisite_merge_unverified')
        self.assertEqual(self.dependency_assignment('downstream','e'*40),grant)
        self.assertEqual(self.forge.compare_requests,[('d'*40,'e'*40)])

    def test_dependency_plan_rejects_cycles_and_retroactive_edits(self):
        self.start()
        self.call('admin','/dependencies',{'task':'a','requires':['b']})
        self.call('admin','/dependencies',{'task':'b','requires':['a']},expected=409)
        self.call('admin','/dependencies',{'task':'a','requires':['c']},expected=409)
        self.call('admin','/dependencies',{'task':'a','requires':['a']},expected=409)
        self.call('admin','/dependencies',{'task':'a','requires':['b','b']},expected=409)
        self.dependency_assignment('b','c'*40)
        self.call('admin','/dependencies',{'task':'b','requires':['c']},expected=409)
        self.assertEqual(self.call('admin','/status')['dependency_plans'],
                         [{'task':'a','requires':['b'],'verified':[],'blocked':['b']}])

    def test_assignment_requires_all_prerequisites_in_its_base(self):
        self.start()
        first=self.dependency_assignment('first','c'*40)
        second=self.dependency_assignment('second','c'*40)
        plan={'task':'join','requires':['second','first']}
        self.call('admin','/dependencies',plan)
        self.prerequisite_integration(first,'d'*40)
        self.assertEqual(self.dependency_assignment('join','e'*40,expected=409)['prerequisites'],['second'])
        self.prerequisite_integration(second,'e'*40,number=8)
        self.assertEqual(self.dependency_assignment('join','e'*40,expected=409)['prerequisites'],['first'])
        self.forge.compare_results[('d'*40,'e'*40)]='ahead'
        self.assertEqual(self.dependency_assignment('join','e'*40)['phase'],'active')
        self.assertEqual(self.call('admin','/dependencies',plan)['blocked'],[])

    def test_dependency_reassignment_during_base_proof_does_not_grant(self):
        self.start()
        upstream=self.dependency_assignment('upstream','c'*40)
        self.prerequisite_integration(upstream,'d'*40)
        self.call('admin','/dependencies',{'task':'downstream','requires':['upstream']})
        self.forge.compare_results[('d'*40,'e'*40)]='ahead'
        self.forge.block_compare=True
        with ThreadPoolExecutor(max_workers=1) as pool:
            grant=pool.submit(self.dependency_assignment,'downstream','e'*40,409)
            try:
                self.assertTrue(self.forge.compare_started.wait(3))
                # The slow provider check cannot hold the authority writer.
                self.call('admin','/assign',{'task':'upstream','expect':1,'owner':'second[bot]',
                    'base':'e'*40,'request':'reassign-during-comparison'})
            finally:
                self.forge.compare_release.set()
            self.assertEqual(grant.result()['error'],'prerequisite_merge_unverified')
        self.assertFalse(any(c['task']=='downstream' for c in self.call('admin','/status')['claims']))

    def test_path_hints_are_durable_advisory_and_use_git_path_boundaries(self):
        self.start()
        first={'task':'first','paths':['src/parser','README.md','out']}
        second={'task':'second','paths':['src/parser/lexer.rs','README.md','output/log','Src/parser']}
        self.call('worker','/path-hints',first,expected=403)
        self.assertEqual(self.call('admin','/path-hints',first)['overlaps'],[])
        self.assertEqual(self.call('admin','/path-hints',second)['overlaps'],[])
        self.dependency_assignment('first','c'*40)
        result=self.call('admin','/path-hints',second)
        self.assertEqual(result['overlaps'],[
            {'task':'first','generation':1,'phase':'active','paths':['README.md','src/parser']}])
        grant=self.dependency_assignment('second','c'*40)
        self.assertEqual(grant['phase'],'active')
        self.assertEqual(self.call('admin','/path-hints',first)['overlaps'],[
            {'task':'second','generation':1,'phase':'active','paths':['README.md','src/parser/lexer.rs']}])
        self.prerequisite_integration(grant,'d'*40)
        self.stop();(self.root/'ready.json').unlink();self.start()
        self.assertEqual(self.call('admin','/status')['path_hints'],
                         {'first':sorted(first['paths']),'second':sorted(second['paths'])})
        self.assertEqual(self.call('admin','/path-hints',first)['overlaps'],[])
        self.call('admin','/path-hints',{'task':'first','paths':[]})
        self.assertNotIn('first',self.call('admin','/status')['path_hints'])

    def test_bad_path_hints_leave_the_previous_declaration_intact(self):
        self.start()
        self.call('admin','/path-hints',{'task':'feature','paths':['ok']})
        for paths in (['../escape'],['/absolute'],['a//b'],['.GiT/config'],['a\\b'],['a','a'],[False]):
            with self.subTest(paths=paths):
                self.call('admin','/path-hints',{'task':'feature','paths':paths},expected=400)
        self.assertEqual(self.call('admin','/status')['path_hints'],{'feature':['ok']})

    def registration(self, task, number, owner='worker[bot]'):
        claim = self.publication(task, owner)
        proposal = self.spec | {key: claim[key] for key in ('task', 'generation', 'claim', 'base')}
        proposal.update(number=number, author=owner, branch=f'agents/test/{number}')
        return {'proposal': proposal, 'capability': claim['capability']}

    def test_registration_admits_under_writer_contention_and_serves_owners_in_turn(self):
        self.config['admission_per_owner'] = 2
        self.config['admission_limit'] = 4
        self.forge.block_publication = True
        self.start()
        blocking = self.publication('blocking')
        jobs = [self.registration('first-a', 11), self.registration('second-a', 12),
                self.registration('first-b', 21, 'second[bot]'),
                self.registration('second-b', 22, 'second[bot]')]
        overflow = self.registration('overflow', 13)
        self.call('worker', '/propose', blocking, expected=202)
        self.assertTrue(self.forge.publication_started.wait(3))
        owners = ['worker', 'worker', 'other', 'other']
        pending = []
        for owner, job in zip(owners, jobs):
            pending.append(self.call(owner, '/register', job, expected=202))
        self.assertEqual(self.call('worker', '/register', jobs[0], expected=202), pending[0])
        changed = jobs[0] | {'proposal': jobs[0]['proposal'] | {'number': 99}}
        self.assertEqual(self.call('worker', '/register', changed, expected=409)['error'],
                         'registration_request_conflict')
        self.assertEqual(self.call('worker', '/register', overflow, expected=429)['error'], 'registration_full')
        self.call('other', pending[0]['status_path'], expected=403)
        self.call('admin', pending[0]['status_path'], expected=403)
        self.call('observer', pending[0]['status_path'], expected=403)
        self.forge.publication_release.set()
        for owner, job, status in zip(owners, jobs, pending):
            self.assertEqual(self.call(owner, status['status_path']), {'registered': job['proposal']['number']})
        self.until(lambda _: (self.root/'service.log').read_text().count('registration_completed') == 4)
        completed = [json.loads(line)['number'] for line in (self.root/'service.log').read_text().splitlines()
                     if 'registration_completed' in line]
        self.assertEqual(completed, [21, 11, 22, 12])
        self.assertEqual(self.call('worker', '/register', jobs[0]), {'registered': 11})
        collision = self.registration('collision', 11, 'second[bot]')
        rejected = self.call('other', '/register', collision, expected=409)
        self.assertEqual(rejected['error'], 'registration_rejected')

    def test_revoked_queue_entry_gets_a_turn_ahead_of_new_registrations(self):
        self.start()
        claim = self.assign()
        self.register(claim)
        self.until(lambda s: s['proposals'][0]['phase'] == 'queued')
        jobs = [self.registration(f'ordinary-{n}', n) for n in range(101, 104)]
        for n in range(101, 104):
            self.forge.extra_frozen_refs.append(f'refs/heads/agents/test/{n}')
        self.forge.track_latest_publication = False
        self.forge.block_publication = True
        self.call('worker', '/propose', self.publication('blocking'), expected=202)
        self.assertTrue(self.forge.publication_started.wait(3))
        for job in jobs:
            self.call('worker', '/register', job, expected=202)
        self.assign(1, owner='second[bot]')
        self.forge.snapshot_numbers.clear()
        self.forge.publication_release.set()
        self.until(lambda s: s['proposals'][0]['phase'] == 'ownership_retired')
        self.assertEqual(self.forge.snapshot_numbers[0], 7)
        self.assertIn(101, self.forge.snapshot_numbers)
        self.assertEqual(self.forge.mutations, ['EnqueueProposal', 'WithdrawProposal', 'CloseProposal'])

    def test_registration_burst_is_accepted_without_writer_lock_retries(self):
        self.config['admission_per_owner'] = 30
        self.forge.block_publication = True
        self.start()
        blocking = self.publication('blocking')
        jobs = [self.registration(f'burst-{i}', 100+i) for i in range(30)]
        self.call('worker', '/propose', blocking, expected=202)
        self.assertTrue(self.forge.publication_started.wait(3))
        with ThreadPoolExecutor(max_workers=12) as pool:
            accepted = list(pool.map(lambda body: self.call('worker', '/register', body, expected=(202, 503)), jobs))
        self.assertTrue(all(r.get('state') == 'queued' for r in accepted), accepted)
        self.assertEqual(len({r['operation'] for r in accepted}), 30)
        self.assertGreater(max(r['poll_after_seconds'] for r in accepted), 1)
        self.forge.publication_release.set()
        for result in accepted:
            self.call('worker', result['status_path'])
        self.assertEqual(len(self.call('admin', '/status')['proposals']), 30)

    def test_queued_registration_survives_restart_and_rechecks_reassignment(self):
        self.forge.block_publication = True
        self.start()
        blocking = self.publication('blocking')
        keep = self.registration('keep', 11)
        stale = self.registration('stale', 12)
        self.call('worker', '/propose', blocking, expected=202)
        self.assertTrue(self.forge.publication_started.wait(3))
        submit = self.client('--no-wait', 'register', payload=keep)
        self.assertEqual(submit.returncode, 0, submit.stderr)
        accepted = json.loads(submit.stdout)
        self.assertEqual(accepted['state'], 'queued')
        revoked = self.call('worker', '/register', stale, expected=202)
        self.call('admin', '/assign', {'task': 'stale', 'expect': 1, 'owner': 'second[bot]',
                                     'base': 'c'*40, 'request': 'replace-stale'})
        self.stop()
        self.forge.publication_release.set()
        (self.root/'ready.json').unlink()
        self.start()
        resume = self.client('--claim', accepted['operation'], 'registration')
        self.assertEqual(resume.returncode, 0, resume.stderr)
        self.assertEqual(json.loads(resume.stdout), {'registered': 11})
        self.assertEqual(self.call('worker', revoked['status_path'], expected=409)['error'], 'assignment_revoked')
        state = self.until(lambda value: all(r['phase'] != 'queued' for r in value['registration_jobs']))
        self.assertEqual([r['number'] for r in state['proposals']], [11])
        self.assertNotIn(keep['capability'], (self.root/'service.log').read_text())

    def test_waiting_owners_get_turns_before_a_flooding_owners_backlog(self):
        self.forge.block_publication=True
        self.start()
        a=self.publication('a1');a2=self.publication('a2');a3=self.publication('a3')
        b=self.publication('b1','second[bot]')
        first=self.call('worker','/propose',a,expected=202)
        self.assertTrue(self.forge.publication_started.wait(3))
        waits=[(who,self.call(who,'/propose',body,expected=202)) for who,body in
               [('worker',a2),('worker',a3),('other',b)]]
        self.call('other',first['status_path'],expected=403)
        self.forge.publication_release.set()
        for who,accepted in waits:self.call(who,accepted['status_path'])
        self.assertEqual(self.forge.publication_titles,
            ['Task a1 attempt 1','Task b1 attempt 1','Task a2 attempt 1','Task a3 attempt 1'])

    def test_capacity_is_retryable_but_changed_requests_are_permanent(self):
        self.config.update(admission_limit=2,admission_per_owner=1)
        self.forge.block_publication=True;self.start()
        a=self.publication('a1');a2=self.publication('a2');b=self.publication('b1','second[bot]')
        self.call('worker','/propose',a,expected=202)
        self.assertTrue(self.forge.publication_started.wait(3))
        self.assertEqual(self.call('worker','/propose',a,expected=202)['operation'],a['claim'])
        denied=self.call('worker','/propose',a|{'request':'changed'},expected=409)
        self.assertEqual(denied,{'error':'publication_request_conflict','retryable':False})
        full=self.call('worker','/propose',a2,expected=429)
        self.assertEqual(full,{'error':'admission_full','retryable':True})
        accepted=self.call('other','/propose',b,expected=202)
        self.forge.publication_release.set();self.call('other',accepted['status_path'])

    def test_global_capacity_applies_across_distinct_owners(self):
        self.config.update(admission_limit=1,admission_per_owner=4)
        self.forge.block_publication=True;self.start()
        first=self.publication('global-first');peer=self.publication('global-peer','second[bot]')
        self.call('worker','/propose',first,expected=202)
        self.assertTrue(self.forge.publication_started.wait(3))
        rejected=self.call('other','/propose',peer,expected=429)
        self.assertEqual(rejected,{'error':'admission_full','retryable':True})
        self.assertEqual(len(self.call('admin','/status')['publication_jobs']),1)
        self.forge.publication_release.set()

    def test_killed_publication_is_unknown_while_queued_work_survives_restart(self):
        self.forge.block_publication=True;self.start()
        a=self.publication('interrupted');b=self.publication('queued')
        accepted=self.call('worker','/propose',a,expected=202)
        self.assertTrue(self.forge.publication_started.wait(3))
        queued=self.call('worker','/propose',b,expected=202)
        self.process.kill();self.process.wait(timeout=5)
        self.forge.publication_release.set();(self.root/'ready.json').unlink();self.start()
        unknown=self.call('worker',accepted['status_path'],expected=409)
        self.assertEqual(unknown,{'error':'publication_outcome_unknown','retryable':False})
        self.call('worker',queued['status_path'])
        self.assertEqual(self.forge.publication_titles,['Task queued attempt 1'])
        self.assertEqual(sum(p.endswith('/git/trees') for p in self.forge.publication_calls),2)

    def test_reassignment_revokes_queued_work_before_it_can_publish(self):
        self.forge.block_publication=True;self.start()
        a=self.publication('active');b=self.publication('revoked','second[bot]')
        self.call('worker','/propose',a,expected=202)
        self.assertTrue(self.forge.publication_started.wait(3))
        queued=self.call('other','/propose',b,expected=202)
        self.call('admin','/assign',{'task':'revoked','expect':1,'owner':'worker[bot]',
            'base':'c'*40,'request':'reassign-revoked'})
        rejected=self.call('other',queued['status_path'],expected=409)
        self.assertEqual(rejected,{'error':'assignment_revoked','retryable':False})
        self.forge.publication_release.set()
        self.call('worker','/propose',a)
        time.sleep(.2)
        self.assertEqual(self.forge.publication_titles,['Task active attempt 1'])

    def client(self,*arguments,payload=None):
        return subprocess.run([sys.executable,str(CLIENT),'--url',self.url,
            '--cert',str(self.pki/'worker.crt'),'--key',str(self.pki/'worker.key'),
            '--ca',str(self.pki/'ca.crt'),'--wait-seconds','5',*arguments],
            input=None if payload is None else json.dumps(payload).encode(),capture_output=True,timeout=8)

    def test_cli_can_return_an_admission_receipt_then_resume_by_claim(self):
        self.forge.block_publication=True;self.start();body=self.publication('cli-resume')
        pending=self.client('--no-wait','propose',payload=body)
        self.assertEqual(pending.returncode,0,pending.stderr)
        accepted=json.loads(pending.stdout)
        self.assertEqual(accepted['operation'],body['claim'])
        self.assertTrue(self.forge.publication_started.wait(3))
        self.forge.publication_release.set()
        completed=self.client('--claim',body['claim'],'publication')
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertEqual(json.loads(completed.stdout)['task'],'cli-resume')

    def test_cli_does_not_retry_an_unknown_publication(self):
        self.forge.drop_publication_reply=True;self.start();body=self.publication('cli-unknown')
        result=self.client('propose',payload=body)
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertEqual(json.loads(result.stdout),{'retryable':False,'error':'publication_outcome_unknown'})
        self.assertEqual(sum(p.endswith('/git/trees') for p in self.forge.publication_calls),1)

    def test_thirty_publications_are_admitted_without_spinning_on_the_writer(self):
        self.config['admission_per_owner']=32
        self.forge.block_publication=True;self.start()
        bodies=[self.publication('load-'+str(i),'worker[bot]' if i%2==0 else 'second[bot]') for i in range(30)]
        first=self.call('worker','/propose',bodies[0],expected=202)
        self.assertTrue(self.forge.publication_started.wait(3))
        def submit(body):
            who='worker' if int(body['task'].split('-')[-1])%2==0 else 'other'
            return who,self.call(who,'/propose',body,expected=202)
        with ThreadPoolExecutor(max_workers=12) as pool:accepted=list(pool.map(submit,bodies[1:]))
        self.assertEqual(len({a['operation'] for _,a in accepted}|{first['operation']}),30)
        self.assertGreater(max(a['poll_after_seconds'] for _, a in accepted), 1)
        self.forge.publication_release.set()
        for who,operation in accepted:self.call(who,operation['status_path'])
        self.assertEqual(len(self.forge.publication_titles),30)

    def test_policy_blocked_admission_resumes_only_after_fresh_observation(self):
        self.config['require_policy_observer']=True;self.forge.allow_observer=True
        self.start();body=self.publication('policy-blocked')
        accepted=self.call('worker','/propose',body,expected=202)
        self.until(lambda s:s['publication_jobs'] and s['publication_jobs'][0]['error']=='publication_precondition_unavailable')
        self.assertEqual(self.forge.publication_calls,[])
        pending=self.call('worker',accepted['status_path'],expected=202)
        self.assertEqual(pending['blocked_reason'],'publication_precondition_unavailable')
        self.policy()
        # Precondition retries are server-owned and paced independently of polls.
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            response=self.call('worker',accepted['status_path'],expected=(200,202))
            if 'number' in response:break
            time.sleep(.1)
        else:self.fail('accepted publication did not resume')
        self.assertEqual(len(self.forge.publication_titles),1)

    def test_blocked_owner_keeps_fifo_order_without_blocking_other_owners(self):
        self.forge.main_failures=1;self.start()
        a=self.publication('first');a2=self.publication('second');b=self.publication('peer','second[bot]')
        accepted=self.call('worker','/propose',a,expected=202)
        self.until(lambda s:s['publication_jobs'] and s['publication_jobs'][0]['error']=='publication_precondition_unavailable')
        self.call('worker','/propose',a2,expected=202)
        peer=self.call('other','/propose',b,expected=202)
        self.call('other',peer['status_path'])
        self.assertEqual(self.forge.publication_titles,['Task peer attempt 1'])
        self.until(lambda s:all(j['phase']=='done' for j in s['publication_jobs']))
        self.assertEqual(self.forge.publication_titles,
            ['Task peer attempt 1','Task first attempt 1','Task second attempt 1'])

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

    def test_missing_reader_is_retryable_and_does_not_use_writer(self):
        self.reader.write_text('')
        self.start()
        self.assertEqual(self.call('worker','/repository-token',expected=503),
                         {'error':'repository_token_unavailable','retryable':True})

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
        denied=self.call('admin','/reconcile-publication',{'claim':body['claim']},expected=409)
        self.assertEqual(denied['error'],'assignment_revoked')
        self.assertEqual(self.forge.mutations,[])
        self.assertEqual(self.call('admin','/status')['proposals'],[])

    def test_reconcile_lost_pr_response_after_restart_from_exact_read_only_proof(self):
        self.forge.drop_publication_reply=True
        self.start()
        body=self.publication('recover-existing')
        pending={'claim':body['claim']}
        self.assertEqual(self.call('worker','/propose',body,expected=409)['error'],'publication_outcome_unknown')
        writes=self.forge.publication_calls[:]
        number,=self.forge.published_prs
        self.assertEqual(self.call('worker','/reconcile-publication',pending,expected=403)['error'],'forbidden')
        self.stop();(self.root/'ready.json').unlink();self.start()
        self.forge.publication_lookup_error=502
        unavailable=self.call('admin','/reconcile-publication',pending,expected=503)
        self.assertEqual(unavailable,{'error':'publication_proof_unavailable','retryable':True})
        self.forge.publication_lookup_error=None
        receipt=self.call('admin','/reconcile-publication',pending)
        self.assertEqual(receipt['number'],number)
        self.assertEqual(receipt['head'],self.forge.published_head)
        self.assertEqual(receipt['claim'],body['claim'])
        self.assertEqual(self.call('admin','/reconcile-publication',pending),receipt)
        self.assertEqual(self.call('worker','/propose',body),receipt)
        self.assertEqual(self.call('worker','/publication?claim='+body['claim']),receipt)
        self.assertEqual(self.forge.publication_calls,writes)
        self.call('worker','/register',{'proposal':receipt,'capability':body['capability']})
        self.until(lambda s:s['proposals'][0]['phase']=='queued')

    def test_reconcile_rejects_absent_ambiguous_or_mismatched_provider_state(self):
        self.forge.drop_publication_reply=True
        self.start()
        body=self.publication('not-provable')
        selector={'claim':body['claim']}
        self.call('worker','/propose',body,expected=409)
        writes=self.forge.publication_calls[:]
        number,=self.forge.published_prs
        pr=self.forge.published_prs.pop(number)

        def deny():
            result=self.call('admin','/reconcile-publication',selector,expected=409)
            self.assertEqual(result['error'],'publication_not_observed')

        deny()
        self.forge.published_prs[number]=pr
        self.forge.published_prs[number+1]=pr | {'number':number+1}
        deny()
        self.forge.published_prs.pop(number+1)
        ref='refs/heads/'+pr['head']['ref']
        self.forge.published_refs[ref]='f'*40
        deny()
        self.forge.published_refs[ref]=pr['head']['sha']
        self.forge.published_prs[number]=pr | {'user':{'type':'Bot','login':'unrelated[bot]'}}
        deny()
        self.assertEqual(self.forge.publication_calls,writes)
        self.assertEqual(self.call('worker','/publication?claim='+body['claim'],expected=409)['error'],
                         'publication_outcome_unknown')

    def test_publisher_rejects_path_and_workflow_escape(self):
        self.start();claim=self.assign()
        body={k:claim[k] for k in ('task','generation','claim','base','capability')}
        body.update(request='publication-path')
        for path in ('../escape','/absolute','.GiT/config','.github/workflows/inject.yml','a\\b',
                     '.github','.GitHub/Workflows','.GitHub/Workflows/gate.yml'):
            self.call('worker','/propose',body|{'files':[{'path':path,'content':'change'}]},expected=400)
        for file in ({'path':'.github/workflows/gate.yml','delete':True},
                     {'path':'.github/workflows/gate.yml','mode':'100755'}):
            self.call('worker','/propose',body|{'files':[file]},expected=400)
        self.assertEqual(self.forge.publication_calls,[])

    def test_publisher_rejects_ambiguous_file_changes(self):
        self.start()
        body = self.publication('invalid-file-shape')
        invalid = [
            [{'path':'a','content':'/w','encoding':'base64'}],
            [{'path':'a','content':'/x==','encoding':'base64'}],
            [{'path':'a','content':'λ','encoding':'base64'}],
            [{'path':'a','content':'x','encoding':'utf-16'}],
            [{'path':'a','delete':1}],
            [{'path':'a','delete':True,'content':'x'}],
            [{'path':'a','mode':'120000','content':'b'}],
            [{'path':'a','sha':'c'*40,'mode':'100644'}],
            [{'path':'a','content':'x'},{'path':'a','delete':True}],
            [{'path':'a/b','content':'x'},{'path':'a','delete':True}],
        ]
        for files in invalid:
            with self.subTest(files=files):
                self.call('worker','/propose',body|{'files':files},expected=400)
        self.assertEqual(self.forge.publication_calls,[])

    def test_mtls_and_role_boundaries(self):
        self.start()
        with self.assertRaises((URLError, ssl.SSLError, ConnectionError)):
            self.call(None,'/claims')
        self.call('unlisted','/claims',expected=403)
        self.call('worker','/assign',{},expected=403)
        self.call('worker','/status',expected=403)
        self.assertEqual(self.call('worker','/claims')['claims'],[])

    def test_broker_lifetime_fence_refuses_a_second_process_and_export(self):
        self.start()
        result=subprocess.run([sys.executable,str(CLI),'--config',str(self.path),
            '--test-api-url',self.forge.url,'--test-token-file',str(self.token),
            '--test-read-token-file',str(self.reader)],capture_output=True,timeout=5)
        self.assertEqual(result.returncode,1)
        self.assertEqual(json.loads(result.stderr),{'event':'startup_failed','error':'AuthorityBusy'})
        target=self.root/'checkpoint.sqlite'
        attempted=self.checkpoint(self.path,'export',str(target))
        self.assertEqual(attempted.returncode,1)
        self.assertEqual(json.loads(attempted.stderr)['error'],'AuthorityBusy')
        self.assertFalse(target.exists())
        self.call('worker','/health')

    def test_checkpoint_restores_claims_and_unknown_intent_without_replay(self):
        self.start()
        self.register(self.assign())
        self.until(lambda s:s['proposals'][0]['phase']=='queued')
        self.forge.drop_publication_reply=True
        body=self.publication('checkpoint-unknown','second[bot]')
        unknown=self.call('other','/propose',body,expected=409)
        self.assertEqual(unknown['error'],'publication_outcome_unknown')
        before=self.call('admin','/status')
        claims=self.call('other','/claims')
        writes=self.forge.publication_calls[:]
        self.stop();(self.root/'ready.json').unlink()
        (Path(self.config['state'])/'queue.sqlite').chmod(0o600)

        archive=self.root/'checkpoint.sqlite'
        exported=self.checkpoint(self.path,'export',str(archive))
        self.assertEqual(exported.returncode,0,exported.stderr)
        self.assertEqual(archive.stat().st_mode & 0o777,0o600)
        digest=json.loads(exported.stdout)['sha256']
        self.assertEqual(digest,hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(self.checkpoint(self.path,'export',str(archive)).returncode,1)

        self.config['state']=str(self.root/'standby')
        different=dict(self.config,manifest=self.config['manifest']|{'freeze_ruleset':99})
        candidate=self.root/'standby.json'
        candidate.write_text(json.dumps(different));candidate.chmod(0o600)
        target=Path(self.config['state'])/'queue.sqlite'
        self.assertEqual(self.checkpoint(candidate,'restore',str(archive),'--sha256',digest).returncode,1)
        self.assertFalse(target.exists())
        candidate.write_text(json.dumps(self.config))
        self.assertEqual(self.checkpoint(candidate,'restore',str(archive),'--sha256','0'*64).returncode,1)
        self.assertFalse(target.exists())
        restored=self.checkpoint(candidate,'restore',str(archive),'--sha256',digest)
        self.assertEqual(restored.returncode,0,restored.stderr)
        self.assertEqual(target.read_bytes(),archive.read_bytes())
        self.assertEqual(self.checkpoint(candidate,'restore',str(archive),'--sha256',digest).returncode,1)
        self.start()
        self.assertEqual(self.call('other','/claims'),claims)
        after=self.call('admin','/status')
        self.assertEqual(after['claims'],before['claims'])
        self.assertEqual(after['publications'],before['publications'])
        self.assertEqual(self.call('other','/propose',body,expected=409),unknown)
        self.assertEqual(self.forge.publication_calls,writes)

    def test_certificate_renewal_holds_same_worker_scope_through_overlap_and_retirement(self):
        def openssl(*args):
            subprocess.run(['openssl',*args],cwd=self.pki,check=True,capture_output=True,timeout=12)
        openssl('req','-newkey','rsa:2048','-nodes','-keyout','renewed.key','-out','renewed.csr','-subj','/CN=worker')
        (self.pki/'renewed.key').chmod(0o600)
        openssl('x509','-req','-in','renewed.csr','-CA','ca.crt','-CAkey','ca.key',
                '-set_serial','16101','-out','renewed.crt','-days','30','-extfile','extensions')
        self.start()
        claim=self.assign()
        original=self.path.read_bytes()
        overlap=self.root/'overlap.json'
        planned=self.renew_worker('renewed',overlap,'overlap')
        self.assertEqual(planned.returncode,0,planned.stderr)
        self.assertEqual(overlap.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.path.read_bytes(),original)
        self.assertEqual(self.renew_worker('renewed',overlap,'overlap').returncode,1)
        self.stop();(self.root/'ready.json').unlink()
        self.config=json.loads(overlap.read_text());self.start()
        self.assertEqual(self.call('worker','/claims'),{'claims':[claim]})
        self.assertEqual(self.call('renewed','/claims'),{'claims':[claim]})
        retired=self.root/'retired.json'
        planned=self.renew_worker('renewed',retired,'retire')
        self.assertEqual(planned.returncode,0,planned.stderr)
        self.stop();(self.root/'ready.json').unlink()
        self.config=json.loads(retired.read_text());self.start()
        self.call('worker','/claims',expected=403)
        self.assertEqual(self.call('renewed','/claims'),{'claims':[claim]})

    def test_certificate_renewal_refuses_other_role_server_leaf_and_early_retire(self):
        self.start()
        path=self.root/'invalid-renewal.json'
        for candidate,phase,error in (
            ('admin','overlap','new certificate belongs to a different principal'),
            ('server','overlap','certificate verification failed'),
            ('unlisted','retire','install the overlap config first'),
        ):
            result=self.renew_worker(candidate,path,phase)
            self.assertEqual(result.returncode,1)
            self.assertEqual(json.loads(result.stderr)['error'],error)
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
