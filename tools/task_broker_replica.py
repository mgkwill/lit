"""Independent broker authority replica and GitHub writer fence."""
import argparse
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
import stat
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, Request, build_opener

import github_queue_coordinator as queue
from github_app_credentials import InstallationToken
from task_broker_checkpoint import staging_file, verify_checkpoint

MAX_SNAPSHOT = 128 * 1024 * 1024
WORKER_READ = {'contents':'read','pull_requests':'read'}
BROKER_READ = WORKER_READ | {'administration':'read','actions':'read','checks':'read'}


class ReplicaUnavailable(OSError):
    """Authority could not be confirmed. Do not start an external effect."""


class Rejected(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Authority:
    def __init__(self, root, manifest, github, reader, broker_reader):
        self.root = root
        self.manifest = manifest
        self.github = github
        self.reader = reader
        self.broker_reader = broker_reader
        self.lock = threading.Lock()
        with closing(self.db()) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS authority (
                id INTEGER PRIMARY KEY CHECK(id=1), holder TEXT NOT NULL, epoch INTEGER NOT NULL,
                revision INTEGER NOT NULL, sha256 TEXT NOT NULL, snapshot BLOB NOT NULL,
                handoff TEXT, binding TEXT NOT NULL)''')
            row = db.execute('SELECT binding FROM authority').fetchone()
            if row and json.loads(row['binding']) != manifest:
                raise ValueError('authority replica repository binding differs')

    def db(self):
        db = sqlite3.connect(self.root / 'replica.sqlite')
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    def validate(self, data):
        with staging_file(self.root) as path:
            path.write_bytes(data)
            verify_checkpoint(path, self.manifest)

    def head(self, row):
        return {name: row[name] for name in ('holder', 'epoch', 'revision', 'sha256')} | {
            'binding':self.manifest}

    def request(self, actor, route, data, epoch, revision):
        # Promotion and provider submission share the same exclusion boundary.
        # A request already sent to GitHub retains its replicated recovery intent.
        with self.lock, closing(self.db()) as db, db:
            row = db.execute('SELECT holder,epoch,revision,sha256,handoff FROM authority WHERE id=1').fetchone()
            if route == '/bootstrap' and actor == 'manager' and row is None:
                holder = json.loads(data[:data.index(b'\n')])['holder']
                if holder not in ('primary', 'standby'):
                    raise Rejected('invalid initial holder')
                snapshot = data[data.index(b'\n')+1:]
                self.validate(snapshot)
                db.execute('INSERT INTO authority VALUES (1,?,1,0,?,?,NULL,?)',
                           (holder, digest(snapshot), snapshot, queue.encoded(self.manifest)))
                return {'holder':holder,'epoch':1,'revision':0,'sha256':digest(snapshot),'binding':self.manifest}
            if row is None:
                raise Rejected('authority is not initialized')
            if route == '/head':
                return self.head(row)
            if route == '/promote' and actor == 'manager':
                change = json.loads(data)
                handoff = queue.encoded(change)
                if row['handoff'] == handoff:
                    return self.head(row)
                if (set(change) != {'from', 'to', 'epoch', 'request'}
                        or change['from'] != row['holder'] or change['epoch'] != row['epoch']
                        or change['to'] not in ('primary', 'standby') or change['to'] == row['holder']
                        or not re.fullmatch(r'[0-9a-f]{32}', change['request'])):
                    raise Rejected('authority promotion precondition differs')
                db.execute('UPDATE authority SET holder=?,epoch=epoch+1,handoff=? WHERE id=1',
                           (change['to'], handoff))
                return self.head(row) | {'holder':change['to'],'epoch':row['epoch']+1}
            if actor != row['holder'] or epoch != row['epoch']:
                raise Rejected('actor is fenced')
            if route == '/snapshot':
                if revision != row['revision']:
                    raise Rejected('snapshot revision changed')
                return bytes(db.execute('SELECT snapshot FROM authority WHERE id=1').fetchone()[0])
            if route == '/checkpoint':
                checksum = digest(data)
                if revision == row['revision'] - 1 and checksum == row['sha256']:
                    return self.head(row)
                if revision != row['revision']:
                    raise Rejected('checkpoint revision differs')
                if checksum != row['sha256']:
                    self.validate(data)
                    db.execute('UPDATE authority SET revision=revision+1,sha256=?,snapshot=? WHERE id=1',
                               (checksum, data))
                    return self.head(row) | {'revision':row['revision']+1,'sha256':checksum}
                return self.head(row)
            if revision != row['revision']:
                raise Rejected('provider request revision differs')
            if route in ('/reader','/broker-reader'):
                token, expires = (self.reader if route=='/reader' else self.broker_reader)()
                return {'token': token, 'expires': expires}
            if route == '/provider':
                return self.provider(json.loads(data))
            raise Rejected('unknown replica operation')

    def provider(self, request):
        github = self.github()
        if request == {'operation':'installation'}:
            return github.api('installation/repositories', writer=True)
        if request.get('operation') == 'queue':
            if set(request) != {'operation','kind','node','head'}:
                raise Rejected('invalid queue operation')
            return github.mutate(request['kind'],request['node'],request['head'])
        if request.get('operation') != 'publish' or set(request) != {'operation','path','payload'}:
            raise Rejected('invalid publication operation')
        path, payload = request['path'], request['payload']
        prefix = 'repos/'+self.manifest['repo']+'/'
        allowed = ('git/blobs','git/trees','git/commits','git/refs','pulls')
        if path not in tuple(prefix+item for item in allowed) or not isinstance(payload,dict):
            raise Rejected('publication target is outside this repository')
        branch = r'agents/broker/attempt/[0-9a-f]{32}'
        if path == prefix+'git/refs' and not re.fullmatch('refs/heads/'+branch,payload.get('ref','')):
            raise Rejected('ref is outside the broker namespace')
        if path == prefix+'pulls' and (payload.get('base') != 'main' or not re.fullmatch(branch,payload.get('head',''))):
            raise Rejected('pull request is outside the broker namespace')
        return github.api(path,payload,writer=True)


class Client:
    def __init__(self, config):
        self.url = config['url']
        origin = urlsplit(self.url)
        if (origin.scheme != 'https' or not origin.hostname or origin.username is not None
                or origin.path or origin.query or origin.fragment):
            raise ValueError('replica requires a pinned HTTPS origin')
        ctx = ssl.create_default_context(cafile=config['ca'])
        ctx.load_cert_chain(config['cert'],config['key'])
        self.opener = build_opener(queue.NoRedirect(),HTTPSHandler(context=ctx))

    def call(self, route, data=None, epoch=None, revision=None, binary=False):
        if isinstance(data,dict):
            data = queue.encoded(data).encode()
        headers = {}
        if epoch is not None:
            headers = {'X-Lit-Epoch':str(epoch),'X-Lit-Revision':str(revision)}
        try:
            with self.opener.open(Request(self.url+route,data,headers),timeout=45) as response:
                if binary:
                    return response.read(MAX_SNAPSHOT+1)
                return json.load(response)
        except HTTPError as error:
            if route == '/provider' and error.headers.get('X-Lit-Provider') == 'github':
                raise queue.ApiError(error.code) from None
            raise ReplicaUnavailable('independent authority unavailable or fenced') from None
        except (OSError,ValueError):
            raise ReplicaUnavailable('independent authority response unavailable') from None


class BrokerReplica:
    def __init__(self, settings, root):
        self.client = Client(settings)
        self.actor = settings['actor']
        self.root = root
        self.state = None
        self.pending = None

    def restore(self, manifest):
        head = self.client.call('/head')
        if head['holder'] != self.actor or head['binding'] != manifest:
            raise ReplicaUnavailable('broker is fenced or repository binding differs')
        data = self.client.call('/snapshot',epoch=head['epoch'],revision=head['revision'],binary=True)
        if len(data)>MAX_SNAPSHOT or digest(data)!=head['sha256']:
            raise ReplicaUnavailable('replicated snapshot digest differs')
        with staging_file(self.root) as path:
            path.write_bytes(data)
            verify_checkpoint(path,manifest)
            with path.open('rb') as source:
                os.fsync(source.fileno())
            if any((self.root/('queue.sqlite'+suffix)).exists() for suffix in ('-wal','-journal','-shm')):
                raise ReplicaUnavailable('local SQLite sidecar requires offline recovery')
            os.replace(path,self.root/'queue.sqlite')
            directory=os.open(self.root,os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        self.state = head

    def confirm(self):
        if self.pending is not None:
            self.flush()
        if self.client.call('/head') != self.state:
            raise ReplicaUnavailable('broker no longer holds replicated authority')

    def flush(self):
        if self.pending is None:
            return
        result = self.send('/checkpoint',self.pending)
        if (result['epoch'] != self.state['epoch'] or result['holder'] != self.actor
                or result['sha256'] != digest(self.pending)):
            raise ReplicaUnavailable('replica acknowledged different authority')
        self.state = result
        self.pending = None

    def sync(self):
        self.flush()
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            path = Path(directory)/'snapshot.sqlite'
            with closing(sqlite3.connect(self.root/'queue.sqlite')) as source, closing(sqlite3.connect(path)) as target:
                source.backup(target)
            self.pending = path.read_bytes()
        if digest(self.pending) == self.state['sha256']:
            self.pending = None
        else:
            self.flush()

    def send(self, path, data=None):
        return self.client.call(path,data,epoch=self.state['epoch'],revision=self.state['revision'])

    def credentials(self):
        result = self.send('/reader')
        return result['token'],result['expires']

    def broker_token(self):
        return self.send('/broker-reader')['token']


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def dispatch(self):
        actor = self.server.actors.get(digest(self.connection.getpeercert(binary_form=True)))
        if actor is None or self.headers.get('Origin'):
            return self.reply(403,{'error':'forbidden'})
        get = ('/head','/snapshot','/reader','/broker-reader')
        post = ('/bootstrap','/checkpoint','/promote','/provider')
        if self.path not in (get if self.command=='GET' else post):
            return self.reply(404,{'error':'unknown_replica_operation'})
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0<=length<=MAX_SNAPSHOT or len(self.headers.get_all('Content-Length',[]))>1 or self.headers.get('Transfer-Encoding'):
                raise Rejected('invalid replica request length')
            data = self.rfile.read(length)
            if len(data)!=length:
                raise Rejected('truncated replica request')
            result = self.server.authority.request(actor,self.path,data,
                int(self.headers.get('X-Lit-Epoch','0')),int(self.headers.get('X-Lit-Revision','-1')))
            return self.reply(200,result)
        except queue.ApiError as error:
            return self.reply(error.code,{'error':'provider_denied'},provider=True)
        except (ValueError,KeyError,TypeError,sqlite3.Error):
            return self.reply(409,{'error':'replica_request_rejected'})
        except (OSError,RuntimeError):
            return self.reply(503,{'error':'replica_unavailable'})

    def reply(self, code, data, provider=False):
        raw = data if isinstance(data,bytes) else queue.encoded(data).encode()
        self.send_response(code)
        self.send_header('Content-Type','application/octet-stream' if isinstance(data,bytes) else 'application/json')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        if provider:
            self.send_header('X-Lit-Provider','github')
        self.end_headers()
        self.wfile.write(raw)

    do_GET = dispatch
    do_POST = dispatch


def main():
    from task_broker import Server, private_file
    from task_ownership import state_lock
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--test-api-url')
    parser.add_argument('--test-token-file',type=Path)
    parser.add_argument('--test-read-token-file',type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(private_file(args.config).read_text())
    root = Path(config['state'])
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    mode = root.lstat().st_mode
    if not stat.S_ISDIR(mode) or mode & 0o077:
        raise ValueError('replica state requires a private directory')
    if (len(config['actors'])!=3 or set(config['actors'].values())!={'manager','primary','standby'}
            or not all(re.fullmatch(r'[0-9a-f]{64}',fingerprint) for fingerprint in config['actors'])):
        raise ValueError('replica needs distinct manager, primary and standby identities')
    repo = config['manifest']['repo']
    if args.test_api_url:
        if not queue.GitHub(args.test_api_url,token='test',observer_token='').local:
            raise ValueError('test provider requires loopback')
        def github():
            return queue.GitHub(args.test_api_url,token=private_file(args.test_token_file).read_text().strip(),observer_token='')
        reader = lambda:(private_file(args.test_read_token_file).read_text().strip(),time.time()+300)
        broker_reader = reader
    else:
        if args.test_token_file or args.test_read_token_file:
            raise ValueError('test credentials require a loopback provider')
        key = private_file(config['app_key'])
        writer = InstallationToken(config['broker_app_id'],config['installation_id'],key,repo)
        reader = InstallationToken(config['broker_app_id'],config['installation_id'],key,repo,
            permissions=WORKER_READ).credentials
        broker_reader = InstallationToken(config['broker_app_id'],config['installation_id'],key,repo,
            permissions=BROKER_READ).credentials
        def github():
            return queue.GitHub('https://api.github.com',token=writer(),observer_token='')
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(config['client_ca'])
    ctx.load_cert_chain(config['server_cert'],str(private_file(config['server_key'])))
    with state_lock(root,'replica-service.lock','replica already running'), Server((config['listen'],config['port']),Handler) as server:
        server.tls = ctx
        server.slots = threading.BoundedSemaphore(16)
        server.authority = Authority(root,config['manifest'],github,reader,broker_reader)
        server.actors = config['actors']
        print(json.dumps({'port':server.server_port}),flush=True)
        server.serve_forever()
    return 0


if __name__ == '__main__':
    sys.exit(main())
