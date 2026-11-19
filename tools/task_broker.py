"""Mutually authenticated TLS boundary for the scheduler-owned queue authority."""
import argparse
from types import SimpleNamespace
import re
import sqlite3
import tempfile
import threading
import time
import github_queue_coordinator as queue
import task_ownership as ownership
from github_app_credentials import InstallationToken
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import ssl
import stat
import sys
from contextlib import nullcontext
from urllib.parse import urlsplit, parse_qs
import broker_admission as admission


def private_file(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError('configuration and private keys must be private regular files')
    return path


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def get_request(self):
        connection, address = self.socket.accept()
        connection.settimeout(5)
        try:
            return self.tls.wrap_socket(connection, server_side=True), address
        except BaseException:
            connection.close()
            raise

    def handle_error(self, request, client_address):
        print(json.dumps({'event':'request_failed'}),flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, data):
        if 'error' in data:data={'retryable':False,**data}
        body=json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if data.get('retryable') or code==202:
            self.send_header('Retry-After',str(data.get('poll_after_seconds',1)))
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self):
        certificate=self.connection.getpeercert(binary_form=True)
        principal=self.server.config['principals'].get(hashlib.sha256(certificate).hexdigest())
        if not principal or self.headers.get('Origin'):
            return self.reply(403,{'error':'forbidden'})
        role=principal['role']
        route=urlsplit(self.path).path
        if ((self.path in ('/assign','/status','/recover-removed') and role != 'scheduler')
                or (self.path == '/policy' and role != 'policy_observer')
                or (route in ('/claims','/repository-token','/register','/propose',
                              '/publication','/registration') and role!='worker')):
            return self.reply(403,{'error':'forbidden'})
        try:
            config=self.server.config
            state=Path(config['state'])
            if self.command == 'GET':
                with queue.database(state) as db:
                    if route in ('/publication','/registration'):
                        query=parse_qs(urlsplit(self.path).query,keep_blank_values=True)
                        if set(query)!={'claim'} or len(query['claim'])!=1 or not re.fullmatch(r'[0-9a-f]{32}',query['claim'][0]):
                            raise admission.Rejected('invalid_'+route[1:]+'_query',400)
                        lookup = admission.status if route=='/publication' else admission.registration_status
                        code,result=lookup(db,principal,query['claim'][0])
                        return self.reply(code,result)
                    if self.path == '/repository-token' and role == 'worker':
                        token=self.server.read_token_source()
                        if not token:raise RuntimeError('read credential unavailable')
                        return self.reply(200,{'token':token,'repository':config['manifest']['repo'],
                            'permissions':{'contents':'read','pull_requests':'read'},
                            'expires_at':getattr(self.server.read_token_source,'expires',time.time()+300)})
                    if self.path == '/claims' and role == 'worker':
                        rows=list(db.execute('SELECT * FROM claims WHERE owner=? ORDER BY generation', (principal['owner'],)))
                        claims=[]
                        for row in rows:
                            with ownership.fence(state,row['task']):
                                fresh=db.execute('SELECT * FROM claims WHERE claim=?',(row['claim'],)).fetchone()
                                claims.append(ownership.receipt(db,fresh))
                        return self.reply(200,{'claims':claims})
                    if self.path == '/status' and role == 'scheduler':
                        return self.reply(200,queue.status(db)|{
                            'registration_jobs':[dict(r) for r in db.execute('SELECT id,claim,owner,phase,error FROM registration_jobs ORDER BY id')],
                            'publication_jobs':[dict(r) for r in db.execute('SELECT id,claim,owner,phase,error FROM publication_jobs ORDER BY id')],
                            'publications':[{'claim':r['claim'],'phase':r['phase'],'receipt':json.loads(r['receipt']) if r['receipt'] else None} for r in db.execute('SELECT * FROM publications')]})
                    if self.path == '/health':
                        return self.reply(200,{'service':'lit-task-broker','last_cycle':self.server.last_cycle,
                                               'last_error':self.server.last_error,'api_version':3})
            if self.command != 'POST' or self.path not in ('/assign','/register','/policy','/recover-removed','/propose'):
                return self.reply(404,{'error':'unknown_route'})
            lengths=self.headers.get_all('Content-Length',[])
            if (len(lengths)!=1 or not lengths[0].isdigit() or not 0<int(lengths[0])<=65536
                    or self.headers.get('Transfer-Encoding')
                    or self.headers.get('Content-Type')!='application/json'):
                return self.reply(400,{'error':'invalid_body'})
            body=json.loads(self.rfile.read(int(lengths[0])))
            if not isinstance(body,dict):
                raise ValueError('expected object')
            if self.path == '/propose':
                if role!='worker':return self.reply(403,{'error':'worker_identity_required'})
                code,result=admission.submit(self.server,principal,body)
                return self.reply(code,result)
            if self.path == '/recover-removed':
                if (set(body)!={'number','event'} or type(body['number']) is not int or body['number']<=0
                        or not isinstance(body['event'],str) or not 0<len(body['event'])<=512):
                    raise ValueError('invalid removal recovery')
                return self.reply(200,recover_removed(self.server,body['number'],body['event']))
            if self.path == '/policy':
                if set(body)!={'observed_at','rules','workers'} or not isinstance(body['observed_at'],(int,float)) or not math.isfinite(body['observed_at']):
                    raise ValueError('invalid observation')
                expected={str(config['manifest'][key]) for key in ('main_ruleset','freeze_ruleset')}|{str(config['publication_ruleset'])}
                if (not isinstance(body['rules'],dict) or set(body['rules'])!=expected
                        or not isinstance(body['workers'],dict) or set(body['workers'])!=set(config['worker_installations'])
                        or abs(time.time()-body['observed_at'])>15
                        or any(not isinstance(rule,dict) or 'bypass_actors' not in rule for rule in body['rules'].values())):
                    raise ValueError('incomplete or stale policy observation')
                with self.server.policy_lock:
                    if self.server.policy and body['observed_at']<=self.server.policy['observed_at']:
                        raise ValueError('replayed policy observation')
                    self.server.policy=body
                    self.server.policy_received=time.monotonic()
                return self.reply(200,{'accepted':True})
            if self.path == '/assign':
                if set(body)!={'task','expect','owner','base','request'}:
                    raise ValueError('invalid assignment')
                if (not isinstance(body['task'],str) or not 0<len(body['task'])<=256
                        or type(body['expect']) is not int or body['expect']<0
                        or not isinstance(body['request'],str) or not 0<len(body['request'])<=256
                        or not isinstance(body['base'],str) or not re.fullmatch(r'[0-9a-f]{40}',body['base'])
                        or body['owner'] not in self.server.owners):
                    return self.reply(400,{'error':'invalid_assignment'})
                with queue.database(state) as db, ownership.fence(state,body['task']):
                    return self.reply(200,ownership.assign(db,SimpleNamespace(**body)))
            code,result=admission.submit_registration(self.server,principal,body)
            return self.reply(code,result)
        except admission.Rejected as error:
            return self.reply(error.status,{'error':error.code,'retryable':error.retryable})
        except ownership.AuthorityBusy:
            return self.reply(503,{'error':'authority_busy','retryable':True})
        except (ValueError,RuntimeError,KeyError,TypeError):
            return self.reply(409,{'error':'request_rejected','retryable':False})
        except (OSError,sqlite3.Error):
            return self.reply(503,{'error':'authority_unavailable','retryable':True})

    do_GET=dispatch
    do_POST=dispatch


def propose(server,principal,body,db=None):
    expected={'task','generation','claim','base','capability','request','files'}
    if (set(body)!=expected or any(not isinstance(body[k],str) for k in ('task','claim','base','capability','request'))
            or not 0<len(body['request'])<=256 or not isinstance(body['files'],list)
            or not 0<len(body['files'])<=32):
        raise ValueError('invalid publication')
    paths=[]
    for file in body['files']:
        if (not isinstance(file,dict) or set(file)!={'path','content'}
                or not isinstance(file['path'],str) or not isinstance(file['content'],str)):
            raise ValueError('expected regular UTF-8 files')
        path=file['path'];parts=path.split('/')
        if (not path or len(path)>1024 or '\\' in path or '\0' in path
                or any(p in ('','.','..') or p.casefold()=='.git' for p in parts)
                or path.startswith('.github/workflows/')):
            raise ValueError('invalid publication path')
        paths.append(path)
    if len(set(paths))!=len(paths):raise ValueError('duplicate publication paths')
    digest=hashlib.sha256(queue.encoded({k:v for k,v in body.items() if k!='capability'}).encode()).hexdigest()
    root=Path(server.config['state'])
    with (nullcontext(db) if db is not None else queue.writer(root)) as db, ownership.fence(root,body['task']):
        binding={k:body[k] for k in ('task','generation','claim','base')}|{'author':principal['owner']}
        claim=ownership.binding(db,binding)
        if (claim['phase']!='active' or claim['owner']!=principal['owner']
                or not hmac.compare_digest(body['capability'].encode(),ownership.token(db,claim['claim']).encode())):
            raise ValueError('publication assignment revoked')
        request_key=queue.encoded([claim['claim'],body['request']])
        previous=db.execute('SELECT * FROM publications WHERE claim=?',(claim['claim'],)).fetchone()
        if previous:
            if previous['request'] not in (body['request'],request_key) or previous['digest']!=digest:
                raise ValueError('publication request changed')
            if previous['phase']=='published':return json.loads(previous['receipt'])
            raise RuntimeError('publication outcome unknown; use a new fenced attempt')
        if claim['proposal'] is not None or db.execute('SELECT 1 FROM publications WHERE request=?',(request_key,)).fetchone():
            raise ValueError('attempt or request already used')
        github=PolicyGitHub(server,server.api_url,server.token_source())
        github.authenticate_writer(server.config['manifest']['repo'],force=True)
        specs=[json.loads(r[0]) for r in db.execute('SELECT spec FROM proposals')]
        queue.policies(github,server.config['manifest'],specs)
        namespace=github.api(f"repos/{server.config['manifest']['repo']}/rulesets/{server.config['publication_ruleset']}")
        if (namespace['target']!='branch' or namespace['enforcement']!='active'
                or namespace['conditions']['ref_name']!={'include':['refs/heads/agents/broker/**/*'],'exclude':[]}
                or {r['type'] for r in namespace['rules']}!={'creation','update','deletion'}
                or namespace.get('bypass_actors')!=[{'actor_id':server.config['broker_app_id'],'actor_type':'Integration','bypass_mode':'exempt'}]):
            raise RuntimeError('broker publication namespace protection required')
        with db:
            db.execute("INSERT INTO publications VALUES (?,?,?,'pending',NULL)",(claim['claim'],request_key,digest))
        repo=server.config['manifest']['repo']
        base=github.api(f"repos/{repo}/git/commits/{claim['base']}")
        tree=github.api(f'repos/{repo}/git/trees',{'base_tree':base['tree']['sha'],
            'tree':[{'path':f['path'],'mode':'100644','type':'blob','content':f['content']} for f in body['files']]},writer=True)
        commit=github.api(f'repos/{repo}/git/commits',{'message':'Task '+claim['task']+' attempt '+str(claim['generation']),
            'tree':tree['sha'],'parents':[claim['base']]},writer=True)
        branch='agents/broker/attempt/'+claim['claim']
        github.api(f'repos/{repo}/git/refs',{'ref':'refs/heads/'+branch,'sha':commit['sha']},writer=True)
        pr=github.api(f'repos/{repo}/pulls',{'head':branch,'base':'main',
            'title':'Task '+claim['task']+' attempt '+str(claim['generation']),
            'body':'Broker publication for a TLS-authenticated task owner. Admission requires a frozen ref and active claim.'},writer=True)
        if (pr['head']['sha']!=commit['sha'] or pr['head']['ref']!=branch
                or pr['user']['login']!=server.config['broker_author']):
            raise RuntimeError('publication identity differs')
        receipt={'number':pr['number'],'head':commit['sha'],'branch':branch,
                 'author':server.config['broker_author'],'agent':principal['owner']}
        receipt.update({k:claim[k] for k in ('task','generation','claim','base')})
        with db:
            db.execute("UPDATE publications SET phase='published',receipt=? WHERE claim=?",(queue.encoded(receipt),claim['claim']))
        return receipt


def recover_removed(server,number,event_id):
    root=Path(server.config['state'])
    with queue.writer(root) as db:
        row=db.execute('SELECT * FROM proposals WHERE number=?',(number,)).fetchone()
        if not row:raise ValueError('unknown proposal')
        spec=json.loads(row['spec'])
        with ownership.fence(root,spec['task']):
            claim=ownership.binding(db,spec)
            if claim['phase']!='active':raise ValueError('assignment is not active')
            key='recovered_queue_event:'+event_id
            receipt={'number':number,'event':event_id,'claim':claim['claim']}
            consumed=db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
            if consumed:
                if consumed[0]!=queue.encoded(receipt):raise ValueError('receipt belongs to another attempt')
                return receipt|{'already_consumed':True}
            if (row['phase'] not in ('queued','awaiting_outcome','removed')
                    or db.execute("SELECT 1 FROM actions WHERE number=? AND status IN ('pending','acknowledged')",(number,)).fetchone()):
                raise ValueError('outcome remains unknown; refusing retry')
            manifest=json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
            github=PolicyGitHub(server,server.api_url,server.token_source())
            github.authenticate_writer(manifest['repo'],force=True)
            specs=[json.loads(r[0]) for r in db.execute('SELECT spec FROM proposals')]
            queue.policies(github,manifest,specs)
            pr=github.snapshot(manifest['repo'],number)['pullRequest']
            events=pr.get('queueEvents',{}).get('nodes',[])
            if (not queue.identity(manifest['repo'],spec,pr) or pr['closed'] or pr['merged']
                    or pr['mergeQueueEntry'] or len(events)!=1):
                raise ValueError('proposal is not safely recoverable')
            event=events[0]
            if (event.get('__typename')!='RemovedFromMergeQueueEvent' or event.get('id')!=event_id
                    or event.get('reason')!='branch_protection_failure'
                    or event.get('actor')!={'__typename':'Bot','login':'github-merge-queue'}):
                raise ValueError('native removal receipt is required')
            with db:
                db.execute('INSERT INTO metadata VALUES (?,?)',(key,queue.encoded(receipt)))
                db.execute("UPDATE proposals SET phase='registered',stale_since=NULL,entry_id=NULL WHERE number=?",(number,))
                db.execute('INSERT INTO events(number,kind,data,created) VALUES (?,?,?,?)',
                           (number,'confirmed_removal_recovered',queue.encoded(receipt),time.time()))
            return receipt|{'already_consumed':False}


class PolicyGitHub(queue.GitHub):
    def __init__(self,server,url,token):
        super().__init__(url,token=token,observer_token='')
        self.server=server

    def observation(self):
        with self.server.policy_lock:
            if not self.server.policy or time.monotonic()-self.server.policy_received>self.server.config.get('policy_ttl_seconds',30):
                raise RuntimeError('fresh authenticated policy observation required')
            for owner,expected in self.server.config['worker_installations'].items():
                worker=self.server.policy.get('workers',{}).get(owner,{})
                if (any(worker.get(k)!=expected[k] for k in ('app_id','installation_id'))
                        or not isinstance(worker.get('suspended_at'),str) or not worker['suspended_at']):
                    raise RuntimeError('worker GitHub write installations must remain suspended')
            return self.server.policy

    def api(self,path,payload=None,writer=False):
        if writer and self.server.config.get('require_policy_observer',True):self.observation()
        actual=super().api(path,payload,writer)
        if '/rulesets/' in path and 'bypass_actors' not in actual:
            observed=self.observation()['rules'].get(path.rsplit('/',1)[-1])
            if not observed or any(actual.get(k)!=observed.get(k) for k in ('target','enforcement','conditions','rules')):
                raise RuntimeError('observed policy differs from live GitHub policy')
            return actual|{'bypass_actors':observed['bypass_actors']}
        return actual

    def mutate(self,kind,node,head):
        if self.server.config.get('require_policy_observer',True):
            self.observation()
        return super().mutate(kind,node,head)


def coordinate(server, token_source, api_url):
    config=server.config;root=Path(config['state'])
    args=SimpleNamespace(state=root,stale_seconds=config.get('stale_seconds',120),test_git_remote=None,max_proposals=4)
    next_tick=0
    while not server.stopping.is_set():
        try:
            for _ in range(8):
                if not admission.run_registration(server):
                    break
            try:
                if time.monotonic() >= next_tick:
                    with queue.writer(root) as db:
                        saved = db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()
                        if saved:
                            manifest = json.loads(saved[0])
                            github = PolicyGitHub(server, api_url, token_source())
                            github.authenticate_writer(manifest['repo'], force=True)
                            queue.tick(db, github, manifest, args)
                    if saved:
                        server.last_cycle = time.time()
                        server.last_error = None
                    next_tick = time.monotonic() + config.get('poll_seconds', 5)
            finally:
                # Publication checks its own policy. A failed reconciliation
                # for one proposal must not suppress every publication turn.
                if not server.stopping.is_set():
                    admission.run_one(server, propose)
            server.stopping.wait(min(.1, config.get('poll_seconds', 5)))
        except Exception as error:
            # No provider errors, URLs, payloads, credentials or private paths in logs/API.
            server.last_error=type(error).__name__
            print(json.dumps({'event':'cycle_blocked','error':server.last_error}),flush=True)
            server.stopping.wait(min(1,config.get('poll_seconds',5)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--ready-file',type=Path)
    parser.add_argument('--test-api-url')
    parser.add_argument('--test-token-file',type=Path)
    parser.add_argument('--test-read-token-file',type=Path)
    parser.add_argument('--cutover-state',action='store_true')
    args=parser.parse_args()
    try:
        config=json.loads(private_file(args.config).read_text())
        manifest=config['manifest']
        if (set(manifest)!={'repo','main_ruleset','freeze_ruleset'}
                or not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+',manifest['repo'])
                or any(type(manifest[k]) is not int or manifest[k]<=0 for k in ('main_ruleset','freeze_ruleset'))
                or type(config['broker_app_id']) is not int or config['broker_app_id']<=0
                or type(config['publication_ruleset']) is not int or config['publication_ruleset']<=0
                or not re.fullmatch(r'[A-Za-z0-9-]+\[bot\]',config['broker_author'])):
            raise ValueError('broker requires pinned repository, identity and policy IDs')
        for owner,worker in config['worker_installations'].items():
            if (not re.fullmatch(r'[A-Za-z0-9-]+\[bot\]',owner)
                    or set(worker)!={'app_id','installation_id'}
                    or any(type(v) is not int or v<=0 for v in worker.values())
                    or worker['app_id']==config['broker_app_id']):
                raise ValueError('invalid worker installation identity')
        if not 0<config.get('policy_ttl_seconds',30)<=30:
            raise ValueError('policy observation lifetime must be at most 30 seconds')
        if config.get('poll_seconds',1)<=0:
            raise ValueError('invalid polling interval')
        if any(type(config.get(k,d)) is not int or not 1<=config.get(k,d)<=1024
               for k,d in (('admission_limit',128),('admission_per_owner',4))):
            raise ValueError('invalid admission limits')
        for fingerprint,principal in config['principals'].items():
            if (not re.fullmatch(r'[0-9a-f]{64}',fingerprint)
                    or principal['role'] not in ('scheduler','worker','policy_observer')
                    or (principal['role']=='worker' and (not isinstance(principal['owner'],str)
                        or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:\[\]-]{0,127}',principal['owner'])))):
                raise ValueError('invalid principal')
        root=Path(config['state'])
        if args.cutover_state:
            with queue.writer(root) as db:
                saved=db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()
                if not saved:raise ValueError('no existing state to cut over')
                old=json.loads(saved[0])
                if ({k:old.get(k) for k in manifest}!=manifest
                        or set(old)!={'repo','main_ruleset','freeze_ruleset','admission_ruleset','broker_app_id'}
                        or old['broker_app_id']!=config['broker_app_id']):
                    raise ValueError('cutover must preserve repository and original policy identity')
                backup=root/'before-credential-cutover.sqlite'
                if backup.exists():raise ValueError('cutover backup already exists')
                with sqlite3.connect(backup) as copy:db.backup(copy)
                backup.chmod(0o600)
                with db:
                    db.execute("UPDATE metadata SET value=? WHERE key='manifest'",(queue.encoded(manifest),))
                    db.execute('INSERT INTO events(number,kind,data,created) VALUES (NULL,?,?,?)',
                        ('credential_separation_cutover',queue.encoded({'previous':old,'workers':config['worker_installations']}),time.time()))
            return 0
        with queue.writer(root) as db:
            db.execute('CREATE TABLE IF NOT EXISTS publications(claim TEXT PRIMARY KEY,request TEXT UNIQUE NOT NULL,digest TEXT NOT NULL,phase TEXT NOT NULL,receipt TEXT)')
            admission.initialize(db)
            saved=db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()
            if saved and json.loads(saved[0])!=manifest:raise ValueError('explicit admission-state cutover required')
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version=ssl.TLSVersion.TLSv1_2
        tls.verify_mode=ssl.CERT_REQUIRED
        tls.load_verify_locations(config['client_ca'])
        tls.load_cert_chain(config['server_cert'],str(private_file(config['server_key'])))
        server=Server((config['listen'],config['port']),Handler)
        server.tls=tls;server.config=config
        server.slots=threading.BoundedSemaphore(32)
        server.owners={p['owner'] for p in config['principals'].values() if p['role']=='worker'}
        server.last_cycle=None;server.last_error=None
        server.policy_lock=threading.Lock();server.policy=None;server.policy_received=0
        server.stopping=threading.Event()
        api_url=args.test_api_url or 'https://api.github.com'
        if args.test_api_url or args.test_token_file or args.test_read_token_file:
            if not args.test_api_url or not args.test_token_file or not queue.GitHub(api_url,token='test',observer_token='').local:
                raise ValueError('test credentials require an explicit loopback forge')
            token_source=lambda:private_file(args.test_token_file).read_text().strip()
            if not args.test_read_token_file:raise ValueError('separate test reader credential required')
            read_token_source=lambda:private_file(args.test_read_token_file).read_text().strip()
        else:
            if config.get('require_policy_observer') is False:
                raise ValueError('production requires a policy observer')
            token_source=InstallationToken(config['broker_app_id'],config['installation_id'],
                private_file(config['app_key']),manifest['repo'])
            read_token_source=InstallationToken(config['broker_app_id'],config['installation_id'],
                private_file(config['app_key']),manifest['repo'],permissions={'contents':'read','pull_requests':'read'})
        server.api_url=api_url;server.token_source=token_source;server.read_token_source=read_token_source
        worker=threading.Thread(target=coordinate,args=(server,token_source,api_url),daemon=True)
        worker.start()
        if args.ready_file:
            # Readers must never observe the empty/partial file created by open.
            with tempfile.NamedTemporaryFile(mode='w',dir=args.ready_file.parent,
                    prefix=args.ready_file.name+'.',delete=False) as ready:
                json.dump({'port':server.server_port},ready)
            Path(ready.name).replace(args.ready_file)
        try:
            server.serve_forever(poll_interval=.1)
        finally:
            server.stopping.set()
            server.server_close()
    except (ValueError,OSError,KeyError,ownership.AuthorityBusy) as error:
        print(json.dumps({'event':'startup_failed','error':type(error).__name__}),file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
