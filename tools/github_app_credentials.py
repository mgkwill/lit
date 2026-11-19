"""Broker-private, repository-scoped GitHub installation-token refresh."""
import base64
from datetime import datetime
import json
from pathlib import Path
import subprocess
import threading
import time
from urllib.request import Request, build_opener
from github_queue_coordinator import NoRedirect


class InstallationToken:
    def __init__(self, app_id, installation_id, key, repo, permissions=None):
        self.app_id=app_id;self.installation_id=installation_id
        self.key=Path(key);self.repo=repo
        self.permissions=permissions or {'contents':'write','pull_requests':'write','administration':'read','actions':'read','checks':'read'}
        self.current=None;self.expires=0
        self.lock=threading.Lock()
        self.retry_after=0
        self.opener=build_opener(NoRedirect())

    def api(self,path,jwt,payload=None):
        request=Request('https://api.github.com/'+path,
            None if payload is None else json.dumps(payload).encode(),
            {'Authorization':'Bearer '+jwt,'Accept':'application/vnd.github+json',
             'Content-Type':'application/json','User-Agent':'lit-task-broker'})
        with self.opener.open(request,timeout=20) as response:
            return json.load(response)

    def __call__(self):
        return self.credentials()[0]

    def credentials(self):
        with self.lock:
            if self.current and time.time()<self.expires-120:
                return self.current,self.expires
            if time.monotonic()<self.retry_after:
                raise RuntimeError('broker installation token refresh unavailable')
            try:
                self.current,self.expires=self._refresh()
            except Exception:
                self.retry_after=time.monotonic()+2
                raise RuntimeError('broker installation token refresh unavailable') from None
            return self.current,self.expires

    def _refresh(self):
        def b64(value):return base64.urlsafe_b64encode(value).rstrip(b'=')
        now=int(time.time())
        message=b64(b'{"alg":"RS256","typ":"JWT"}')+b'.'+b64(json.dumps({
            'iat':now-30,'exp':now+300,'iss':str(self.app_id)}).encode())
        signature=subprocess.run(['openssl','dgst','-sha256','-sign',str(self.key)],
            input=message,capture_output=True,timeout=10)
        if signature.returncode:
            raise RuntimeError('broker signing unavailable')
        jwt=(message+b'.'+b64(signature.stdout)).decode()
        installed=self.api(f'app/installations/{self.installation_id}',jwt)
        if (installed['app_id']!=self.app_id or installed['account']['login']!=self.repo.split('/')[0]
                or installed['repository_selection']!='selected' or installed.get('suspended_at')):
            raise RuntimeError('broker installation identity differs or is suspended')
        issued=self.api(f'app/installations/{self.installation_id}/access_tokens',jwt,{
            'repositories':[self.repo.split('/')[1]],'permissions':self.permissions})
        if ([r['full_name'] for r in issued.get('repositories',[])]!=[self.repo]
                or issued['repository_selection']!='selected'
                or issued.get('permissions')!=self.permissions|{'metadata':'read'}):
            raise RuntimeError('broker token repository scope differs')
        token=issued['token']
        expires=datetime.fromisoformat(issued['expires_at'].replace('Z','+00:00')).timestamp()
        if not isinstance(token,str) or not token or expires<=time.time()+120:
            raise RuntimeError('issued broker token has insufficient lifetime')
        return token,expires
