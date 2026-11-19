"""Concurrent and fail-closed GitHub App credential refresh regression tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from threading import Barrier
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from github_app_credentials import InstallationToken


class FixtureToken(InstallationToken):
    def __init__(self):
        super().__init__(99,7,'unused.key','100latent/sandbox',
                         permissions={'contents':'read','pull_requests':'read'})
        self.reads=0
        self.mints=[]
        self.fail=False
        self.lifetime=timedelta(hours=1)

    def api(self,path,jwt,payload=None):
        if path=='app/installations/7':
            self.reads+=1
            time.sleep(.05)
            if self.fail:
                raise OSError('injected request failure')
            return {'app_id':99,'account':{'login':'100latent'},
                    'repository_selection':'selected','suspended_at':None}
        if path!='app/installations/7/access_tokens':
            raise AssertionError('unexpected credential path')
        self.mints.append(payload)
        return {'token':f'fixture-{len(self.mints)}',
                'expires_at':(datetime.now(timezone.utc)+self.lifetime).isoformat(),
                'repositories':[{'full_name':'100latent/sandbox'}],
                'repository_selection':'selected','permissions':self.permissions|{'metadata':'read'}}


class CredentialTests(unittest.TestCase):
    def setUp(self):
        signer=patch('github_app_credentials.subprocess.run',
                     return_value=SimpleNamespace(returncode=0,stdout=b'fixture signature'))
        self.addCleanup(signer.stop)
        self.signer=signer.start()
        self.token=FixtureToken()

    def concurrent(self, callback):
        workers=24
        barrier=Barrier(workers)
        def start(_):
            barrier.wait(timeout=5)
            return callback()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(start,range(workers)))

    def test_concurrent_callers_share_one_refresh_and_one_expiry(self):
        replies=self.concurrent(self.token.credentials)
        self.assertEqual(len(set(replies)),1)
        token,expires=replies[0]
        self.assertEqual(token,'fixture-1')
        self.assertGreater(expires,time.time()+120)
        self.assertEqual(self.token(),token)
        self.assertEqual(self.token.reads,1)
        self.assertEqual(self.signer.call_count,1)
        self.assertEqual(self.token.mints,[
            {'repositories':['sandbox'],'permissions':{'contents':'read','pull_requests':'read'}}])

    def test_failed_refresh_withholds_near_expiry_token_and_bounds_provider_calls(self):
        self.assertEqual(self.token(),'fixture-1')
        self.token.expires=time.time()+90
        self.token.fail=True
        def denied():
            with self.assertRaisesRegex(RuntimeError,'token refresh unavailable'):
                self.token()
        self.concurrent(denied)
        self.assertEqual(self.signer.call_count,2)
        self.assertEqual(self.token.reads,2)
        self.assertEqual(len(self.token.mints),1)
        self.token.fail=False
        time.sleep(2.05)
        self.assertEqual(self.token(),'fixture-2')
        self.assertEqual(self.signer.call_count,3)

    def test_too_short_lived_credential_is_never_cached(self):
        self.token.lifetime=timedelta(seconds=30)
        with self.assertRaisesRegex(RuntimeError,'token refresh unavailable'):
            self.token()
        self.assertIsNone(self.token.current)


if __name__=='__main__':
    unittest.main()
