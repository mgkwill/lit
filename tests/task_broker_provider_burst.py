"""Exercise provider fault recovery with 100 mTLS workers and a loopback forge."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import platform
import ssl
import subprocess
import time
import unittest

from task_broker_regression import BrokerTests


class ProviderBurst(BrokerTests):
    def worker_identity(self, index):
        name = f'provider-worker-{index:03d}'
        key, csr, cert = (self.pki/(name+ext) for ext in ('.key','.csr','.crt'))
        subprocess.run(['openssl','req','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256',
                        '-nodes','-keyout',str(key),'-out',str(csr),'-subj','/CN='+name],
                       check=True,capture_output=True)
        subprocess.run(['openssl','x509','-req','-in',str(csr),'-CA',str(self.pki/'ca.crt'),
                        '-CAkey',str(self.pki/'ca.key'),'-set_serial',str(1000+index),
                        '-days','2','-extfile',str(self.pki/'extensions'),'-out',str(cert)],
                       check=True,capture_output=True)
        key.chmod(0o600)
        fingerprint=hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
        return name, fingerprint

    def wait_jobs(self, predicate, seconds=60):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            jobs=self.call('admin','/status')['publication_jobs']
            phases=Counter(job['phase'] for job in jobs)
            if predicate(jobs,phases):
                return phases
            time.sleep(.1)
        self.fail(f'publication phases failed to converge: {dict(phases)}')

    def test_provider_recovery_for_100_owners(self):
        size=100
        with ThreadPoolExecutor(max_workers=12) as pool:
            identities=list(pool.map(self.worker_identity,range(size)))
        self.assertEqual(len({fp for _,fp in identities}),size)
        for name,fp in identities:
            self.config['principals'][fp]={'role':'worker','owner':name}
        self.config['admission_per_owner']=1
        self.config['admission_limit']=size
        self.forge.track_latest_publication=False
        self.forge.main_fault_status=429
        self.start()
        operations=[(name,self.publication(name,name)) for name,_ in identities]

        def submit(op):
            name,body=op
            return self.call(name,'/propose',body,expected=202)

        with ThreadPoolExecutor(max_workers=16) as pool:
            accepted=list(pool.map(submit,operations))
        self.assertEqual(len({r['operation'] for r in accepted}),size)
        self.wait_jobs(lambda jobs, phases: phases=={'queued':size} and
                       any(r['error']=='publication_precondition_unavailable' for r in jobs))
        self.assertIn(429,self.forge.main_faults)
        self.forge.main_fault_status=503
        self.wait_jobs(lambda jobs, phases: phases=={'queued':size} and 503 in self.forge.main_faults)
        self.assertEqual(self.forge.publication_calls,[])

        # These responses fail after the forge applied the PR creation.
        fault_owners={identities[8][0]:'disconnect',identities[56][0]:502}
        self.forge.publication_response_faults={f'Task {owner} attempt 1':fault
                                                for owner,fault in fault_owners.items()}
        self.forge.main_fault_status=None
        phases=self.wait_jobs(lambda jobs,counts: counts=={'done':size-2,'unknown':2})
        self.assertEqual(Counter(self.forge.publication_titles),
                         {f'Task {owner} attempt 1':1 for owner,_ in identities})
        writes=self.forge.publication_calls[:]
        self.assertEqual(Counter(path.rsplit('/',1)[1] for path in writes),
                         {kind:size for kind in ('trees','commits','refs','pulls')})

        self.stop()
        (self.root/'ready.json').unlink()
        self.start()

        def check_replay(pair):
            (name,body),admission=pair
            expected=409 if name in fault_owners else 200
            receipt=self.call(name,admission['status_path'],expected=expected)
            self.assertEqual(self.call(name,'/propose',body,expected=expected),receipt)
            if expected==409:
                self.assertEqual(receipt,{'error':'publication_outcome_unknown','retryable':False})
            else:
                self.assertEqual(receipt['agent'],name)
                self.assertEqual(receipt['claim'],body['claim'])

        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(check_replay,zip(operations,accepted)))
        self.assertEqual(self.forge.publication_calls,writes)
        self.assertEqual(self.forge.mutations,[])
        self.wait_jobs(lambda jobs,counts:counts==phases)
        print(json.dumps({
            'system':platform.system(),'architecture':platform.machine(),
            'distinct_tls_owners':size,'concurrent_clients':16,
            'admitted_during_provider_outage':len(accepted),
            'failed_precondition_reads':dict(Counter(self.forge.main_faults)),
            'mutations_during_precondition_outage':0,'terminal_phases':dict(phases),
            'post_apply_faults':['disconnect','http_502'],
            'provider_pr_creations':len(self.forge.publication_titles),
            'provider_write_calls':len(writes),'writes_after_restart_and_replay':0,
        }),flush=True)


if __name__=='__main__':
    tests=unittest.TestSuite([ProviderBurst('test_provider_recovery_for_100_owners')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(tests).wasSuccessful())
