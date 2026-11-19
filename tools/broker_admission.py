"""Durable bounded broker admission with round-robin turns per logical owner.

Only queued jobs may start. A process interrupted in 'running' never replays a
GitHub mutation, even if the interruption preceded the first provider call.
"""
import base64
import hashlib
import hmac
import json
import re
import math
import time
from pathlib import Path
import github_queue_coordinator as queue
import task_ownership as ownership


REGULAR_MODES = ('100644', '100755')


class Rejected(RuntimeError):
    def __init__(self, code, status=409, retryable=False):
        self.code, self.status, self.retryable = code, status, retryable
        super().__init__(code)


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS publication_jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, claim TEXT UNIQUE NOT NULL,
        owner TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
        phase TEXT NOT NULL, error TEXT, next_at REAL NOT NULL DEFAULT 0)''')
    db.execute("""CREATE TABLE IF NOT EXISTS registration_jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, claim TEXT UNIQUE NOT NULL,
        owner TEXT NOT NULL, spec TEXT NOT NULL, phase TEXT NOT NULL, error TEXT)""")
    # Published receipts are authoritative; everything else running at restart
    # is uncertain. Queued jobs have no external intent and remain executable.
    with db:
        db.execute("""UPDATE publication_jobs SET phase=CASE WHEN EXISTS(
            SELECT 1 FROM publications p WHERE p.claim=publication_jobs.claim
            AND p.phase='published' AND p.digest=publication_jobs.digest) THEN 'done' ELSE 'unknown' END,
            error='publication_outcome_unknown' WHERE phase='running'""")


def request(body):
    expected={'task','generation','claim','base','capability','request','files'}
    if (set(body)!=expected or any(not isinstance(body[k],str) for k in ('task','claim','base','capability','request'))
            or type(body['generation']) is not int or body['generation']<=0
            or not re.fullmatch(r'[0-9a-f]{32}',body['claim'])
            or not re.fullmatch(r'[0-9a-f]{40}',body['base'])
            or not 0<len(body['task'])<=256 or not 0<len(body['request'])<=256
            or not isinstance(body['files'],list) or not 0<len(body['files'])<=32):
        raise Rejected('invalid_publication',400)
    paths=set()
    for file in body['files']:
        if not isinstance(file,dict) or not isinstance(file.get('path'),str):
            raise Rejected('invalid_publication',400)
        path=file['path']
        parts=[p.casefold().rstrip(' .') for p in path.split('/')]
        if (len(path)>1024 or '\\' in path or '\0' in path
                or any(p in ('','.git','git~1') for p in parts)
                or parts==['.github'] or parts[:2]==['.github','workflows']):
            raise Rejected('invalid_publication_path',400)
        if path in paths:
            raise Rejected('duplicate_publication_path',400)
        paths.add(path)
        validate_file(file)
    for path in paths:
        parts=path.split('/')
        if any('/'.join(parts[:i]) in paths for i in range(1,len(parts))):
            raise Rejected('overlapping_publication_paths',400)
    public={k:v for k,v in body.items() if k!='capability'}
    return public, hashlib.sha256(queue.encoded(public).encode()).hexdigest()


def validate_file(file):
    try:
        file['path'].encode('utf-8')
        if set(file)=={'path','delete'} and file['delete'] is True:
            return
        if 'mode' in file and file['mode'] not in REGULAR_MODES:
            raise ValueError
        if set(file)=={'path','mode'}:
            return
        if (set(file)-{'path','content','encoding','mode'}
                or not isinstance(file.get('content'),str)):
            raise ValueError
        encoding=file.get('encoding','utf-8')
        content=file['content'].encode('utf-8')
        if encoding=='utf-8':
            return
        if encoding=='base64' and base64.b64encode(base64.b64decode(content,validate=True))==content:
            return
    except ValueError:
        pass
    raise Rejected('invalid_publication',400)


def active(db, principal, claim_id, capability=None):
    row=db.execute('SELECT * FROM claims WHERE claim=?',(claim_id,)).fetchone()
    if not row or row['owner']!=principal['owner']:raise Rejected('wrong_owner',403)
    if row['phase']!='active':raise Rejected('assignment_revoked')
    if capability is not None and not hmac.compare_digest(capability.encode(),ownership.token(db,claim_id).encode()):
        raise Rejected('invalid_capability',403)
    return row


def poll_delay(db, *, publication):
    """Pace read traffic during a burst while keeping small queues responsive."""
    if publication:
        backlog = db.execute("SELECT count(*) FROM publication_jobs WHERE phase IN ('queued','running')").fetchone()[0]
        return min(12, max(1, (backlog + 3) // 4))
    backlog = db.execute("SELECT count(*) FROM registration_jobs WHERE phase='queued'").fetchone()[0]
    return min(6, max(1, (backlog + 7) // 8))


def status(db,principal,claim_id):
    active(db,principal,claim_id)
    job=db.execute('SELECT * FROM publication_jobs WHERE claim=?',(claim_id,)).fetchone()
    if not job:raise Rejected('publication_not_found',404)
    if job['phase']=='done':
        published=db.execute("SELECT receipt FROM publications WHERE claim=? AND phase='published' AND digest=?",(claim_id,job['digest'])).fetchone()
        if not published:raise Rejected('publication_outcome_unknown')
        return 200,json.loads(published[0])
    if job['phase'] in ('unknown','rejected'):
        raise Rejected(job['error'] or 'publication_outcome_unknown')
    return 202,{'operation':claim_id,'state':job['phase'],'retryable':False,
        'poll_after_seconds':max(poll_delay(db, publication=True), min(5, math.ceil(job['next_at']-time.time()))),
        'status_path':'/publication?claim='+claim_id,
        'blocked_reason':job['error']}


def submit(server,principal,body):
    payload,digest=request(body)
    root=Path(server.config['state'])
    with queue.database(root) as db:
        # A short SQLite transaction admits requests even while remote writes
        # hold the coordinator writer lock. Capabilities never enter this table.
        db.execute('BEGIN IMMEDIATE')
        try:
            row=active(db,principal,body['claim'],body['capability'])
            if any(row[k]!=body[k] for k in ('task','generation','base')):raise Rejected('assignment_mismatch')
            job=db.execute('SELECT * FROM publication_jobs WHERE claim=?',(row['claim'],)).fetchone()
            if job:
                if job['digest']!=digest:raise Rejected('publication_request_conflict')
            else:
                previous=db.execute('SELECT * FROM publications WHERE claim=?',(row['claim'],)).fetchone()
                if previous:
                    if previous['digest']!=digest:raise Rejected('publication_request_conflict')
                    phase='done' if previous['phase']=='published' else 'unknown'
                else:
                    phase='queued'
                    total=db.execute("SELECT count(*) FROM publication_jobs WHERE phase IN ('queued','running')").fetchone()[0]
                    own=db.execute("SELECT count(*) FROM publication_jobs WHERE owner=? AND phase IN ('queued','running')",(principal['owner'],)).fetchone()[0]
                    if total>=server.config.get('admission_limit',128) or own>=server.config.get('admission_per_owner',4):
                        raise Rejected('admission_full',429,True)
                    if row['proposal'] is not None:raise Rejected('attempt_already_bound')
                db.execute('INSERT INTO publication_jobs(claim,owner,digest,payload,phase,error) VALUES (?,?,?,?,?,?)',
                    (row['claim'],row['owner'],digest,queue.encoded(payload),phase,
                     'publication_outcome_unknown' if phase=='unknown' else None))
            db.commit()
        except BaseException:
            db.rollback();raise
        return status(db,principal,row['claim'])


def run_one(server,publish):
    root=Path(server.config['state'])
    with queue.writer(root) as db:
        heads={}
        for row in db.execute("SELECT * FROM publication_jobs WHERE phase='queued' ORDER BY id"):
            heads.setdefault(row['owner'],row)
        ready=[row for row in heads.values() if row['next_at']<=time.time()]
        job = next_owner_job(db, ready, 'admission_last_owner')
        if not job:
            return False
        owner = job['owner']
        with db:
            db.execute("UPDATE publication_jobs SET phase='running' WHERE id=?", (job['id'],))
            save_turn(db, 'admission_last_owner', owner)
        try:
            if owner not in server.owners:raise Rejected('identity_revoked')
            claim=active(db,{'owner':owner},job['claim'])
            body=json.loads(job['payload'])|{'capability':ownership.token(db,claim['claim'])}
            publish(server,{'owner':owner},body,db=db)
        except Exception as error:
            previous=db.execute('SELECT phase,digest FROM publications WHERE claim=?',(job['claim'],)).fetchone()
            if previous:
                phase='done' if previous['phase']=='published' and previous['digest']==job['digest'] else 'unknown'
                code=None if phase=='done' else 'publication_outcome_unknown'
            elif isinstance(error,Rejected):
                phase='rejected';code=error.code
            else:
                # No publication intent exists: no provider mutation was started.
                phase='queued';code='publication_precondition_unavailable'
                try:active(db,{'owner':owner},job['claim'])
                except Rejected as revoked:phase='rejected';code=revoked.code
            with db:db.execute('UPDATE publication_jobs SET phase=?,error=?,next_at=? WHERE id=?',
                              (phase,code,time.time()+5,job['id']))
        else:
            with db:db.execute("UPDATE publication_jobs SET phase='done',error=NULL,payload='{}' WHERE id=?",(job['id'],))
        return True



def next_owner_job(db, jobs, cursor):
    """Take the oldest eligible job for the next owner in cyclic order."""
    heads = {}
    for job in jobs:
        heads.setdefault(job['owner'], job)
    if not heads:
        return None
    last = db.execute("SELECT value FROM metadata WHERE key=?", (cursor,)).fetchone()
    owners = sorted(heads)
    owner = next((name for name in owners if not last or name > last[0]), owners[0])
    return heads[owner]


def save_turn(db, cursor, owner):
    db.execute("""INSERT INTO metadata VALUES (?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (cursor, owner))


def registration_status(db, principal, claim_id):
    active(db, principal, claim_id)
    job = db.execute("SELECT spec,phase,error FROM registration_jobs WHERE claim=?", (claim_id,)).fetchone()
    if not job:
        raise Rejected('registration_not_found', 404)
    if job['phase'] == 'done':
        return 200, {'registered': json.loads(job['spec'])['number']}
    if job['phase'] == 'rejected':
        raise Rejected(job['error'])
    return 202, {'operation': claim_id, 'state': 'queued', 'retryable': False,
                 'poll_after_seconds': poll_delay(db, publication=False), 'status_path': '/registration?claim='+claim_id}


def verify_registration(db, config, principal, spec, capability=None):
    claim = active(db, principal, spec['claim'], capability)
    if any(claim[k] != spec[k] for k in ('task', 'generation', 'base')):
        raise Rejected('assignment_mismatch')
    if spec['author'] != principal['owner']:
        published = db.execute("SELECT receipt FROM publications WHERE claim=? AND phase='published'",
                               (spec['claim'],)).fetchone()
        if spec['author'] != config['broker_author'] or not published or json.loads(published[0]) != spec:
            raise Rejected('unproven_broker_publication', 403)
    return claim


def submit_registration(server, principal, body):
    required = {'number', 'head', 'branch', 'author', 'task', 'generation', 'claim', 'base'}
    spec = body.get('proposal')
    if (set(body) != {'proposal', 'capability'} or not isinstance(body['capability'], str)
            or not isinstance(spec, dict) or spec.keys() not in (required, required | {'agent'})
            or any(not isinstance(spec[k], str) for k in required - {'number', 'generation'})
            or type(spec['generation']) is not int or not re.fullmatch(r'[0-9a-f]{32}', spec['claim'])):
        raise Rejected('invalid_registration')
    owner = principal['owner']
    if spec.get('agent', spec['author']) != owner:
        raise Rejected('wrong_owner', 403)
    config = server.config
    queue.validate_registration(config['manifest'], [spec])
    claim_id = spec['claim']
    encoded = queue.encoded(spec)
    with queue.database(Path(config['state'])) as db:
        with db:
            db.execute('BEGIN IMMEDIATE')
            claim = verify_registration(db, config, principal, spec, body['capability'])
            existing = db.execute("SELECT spec FROM registration_jobs WHERE claim=?", (claim_id,)).fetchone()
            if existing:
                if existing['spec'] != encoded:
                    raise Rejected('registration_request_conflict')
            elif claim['proposal'] is not None:
                saved = db.execute("SELECT spec FROM proposals WHERE number=?", (claim['proposal'],)).fetchone()
                if not saved or saved['spec'] != encoded:
                    raise Rejected('attempt_already_bound')
                return 200, {'registered': claim['proposal']}
            else:
                total, own = db.execute("""SELECT count(*), count(CASE WHEN owner=? THEN 1 END)
                    FROM registration_jobs WHERE phase='queued'""", (owner,)).fetchone()
                if total >= config.get('admission_limit', 128) or own >= config.get('admission_per_owner', 4):
                    raise Rejected('registration_full', 429, True)
                db.execute("INSERT INTO registration_jobs(claim,owner,spec,phase) VALUES (?,?,?,'queued')",
                           (claim_id, owner, encoded))
        return registration_status(db, principal, claim_id)


def run_registration(server):
    config = server.config
    root = Path(config['state'])
    cursor = 'registration_last_owner'
    with queue.writer(root) as db:
        jobs = db.execute("SELECT * FROM registration_jobs WHERE phase='queued' ORDER BY id")
        job = next_owner_job(db, jobs, cursor)
        if not job:
            return False
        owner, claim_id = job['owner'], job['claim']
        spec = json.loads(job['spec'])
        try:
            with ownership.fence(root, spec['task']), db:
                db.execute('BEGIN IMMEDIATE')
                if owner not in server.owners:
                    raise Rejected('identity_revoked')
                verify_registration(db, config, {'owner': owner}, spec)
                queue.register_specs(db, config['manifest'], [spec],
                                     {claim_id: ownership.token(db, claim_id)})
                db.execute("UPDATE registration_jobs SET phase='done' WHERE id=?", (job['id'],))
                save_turn(db, cursor, owner)
        except ownership.AuthorityBusy:
            with db:
                save_turn(db, cursor, owner)
        except (Rejected, ValueError) as error:
            code = error.code if isinstance(error, Rejected) else 'registration_rejected'
            with db:
                db.execute("UPDATE registration_jobs SET phase='rejected',error=? WHERE id=?",
                           (code, job['id']))
                save_turn(db, cursor, owner)
        else:
            queue.output(event='registration_completed', number=spec['number'])
        return True
