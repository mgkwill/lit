"""Durable bounded publication admission; one round-robin turn per logical owner.

Only queued jobs may start. A process interrupted in 'running' never replays a
GitHub mutation, even if the interruption preceded the first provider call.
"""
import hashlib
import hmac
import json
import re
import math
import time
from pathlib import Path
import github_queue_coordinator as queue
import task_ownership as ownership


class Rejected(RuntimeError):
    def __init__(self, code, status=409, retryable=False):
        self.code, self.status, self.retryable = code, status, retryable
        super().__init__(code)


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS publication_jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, claim TEXT UNIQUE NOT NULL,
        owner TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
        phase TEXT NOT NULL, error TEXT, next_at REAL NOT NULL DEFAULT 0)''')
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
    paths=[]
    for file in body['files']:
        if (not isinstance(file,dict) or set(file)!={'path','content'}
                or not isinstance(file['path'],str) or not isinstance(file['content'],str)):
            raise Rejected('invalid_publication',400)
        path=file['path']
        if (not path or len(path)>1024 or '\\' in path or '\0' in path
                or any(p in ('','.','..') or p.casefold()=='.git' for p in path.split('/'))
                or path.startswith('.github/workflows/')):
            raise Rejected('invalid_publication_path',400)
        # Reject invalid Unicode before a job can reach the provider boundary.
        try: path.encode('utf-8');file['content'].encode('utf-8')
        except UnicodeError: raise Rejected('invalid_publication',400) from None
        paths.append(path)
    if len(set(paths))!=len(paths):raise Rejected('duplicate_publication_path',400)
    public={k:v for k,v in body.items() if k!='capability'}
    return public, hashlib.sha256(queue.encoded(public).encode()).hexdigest()


def active(db, principal, claim_id, capability=None):
    row=db.execute('SELECT * FROM claims WHERE claim=?',(claim_id,)).fetchone()
    if not row or row['owner']!=principal['owner']:raise Rejected('wrong_owner',403)
    if row['phase']!='active':raise Rejected('assignment_revoked')
    if capability is not None and not hmac.compare_digest(capability.encode(),ownership.token(db,claim_id).encode()):
        raise Rejected('invalid_capability',403)
    return row


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
        'poll_after_seconds':max(1,min(5,math.ceil(job['next_at']-time.time()))),
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
        if not ready:return False
        last=db.execute("SELECT value FROM metadata WHERE key='admission_last_owner'").fetchone()
        owners=sorted({r['owner'] for r in ready})
        owner=next((o for o in owners if not last or o>last[0]),owners[0])
        job=next(r for r in ready if r['owner']==owner)
        with db:
            db.execute("UPDATE publication_jobs SET phase='running' WHERE id=?",(job['id'],))
            db.execute("INSERT INTO metadata VALUES ('admission_last_owner',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(owner,))
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
