"""Scheduler-owned task authority; workers receive capabilities, never this database."""
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import secrets
import time


class AuthorityBusy(RuntimeError):
    """No operation started; the caller may retry the same request."""


class DependenciesBlocked(RuntimeError):
    def __init__(self, code, prerequisites):
        self.code = code
        self.prerequisites = prerequisites
        super().__init__(code)


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS tasks(task TEXT PRIMARY KEY, generation INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS claims(
        claim TEXT PRIMARY KEY, task TEXT NOT NULL, generation INTEGER NOT NULL,
        owner TEXT NOT NULL, base TEXT NOT NULL, phase TEXT NOT NULL,
        proposal INTEGER UNIQUE, request TEXT UNIQUE NOT NULL, request_spec TEXT NOT NULL,
        UNIQUE(task,generation));
      CREATE TABLE IF NOT EXISTS task_dependencies(
        task TEXT NOT NULL, prerequisite TEXT NOT NULL, PRIMARY KEY(task,prerequisite));
    ''')


def plan_dependencies(db, task, prerequisites):
    names = sorted(prerequisites)
    if task in names or len(set(names)) != len(names):
        raise ValueError('duplicate or self dependency')
    db.execute('BEGIN IMMEDIATE')
    with db:
        existing = [r[0] for r in db.execute(
            'SELECT prerequisite FROM task_dependencies WHERE task=? ORDER BY prerequisite', (task,))]
        if existing:
            if existing != names:
                raise ValueError('task dependency plan is immutable')
        else:
            if db.execute('SELECT 1 FROM tasks WHERE task=?', (task,)).fetchone():
                raise ValueError('declare dependencies before the first assignment')
            db.executemany('INSERT INTO task_dependencies VALUES (?,?)', ((task, name) for name in names))
            cycle = db.execute('''WITH RECURSIVE upstream(name) AS (
                SELECT prerequisite FROM task_dependencies WHERE task=?
                UNION
                SELECT d.prerequisite FROM task_dependencies d JOIN upstream u ON d.task=u.name
                ) SELECT 1 FROM upstream WHERE name=?''', (task, task)).fetchone()
            if cycle:
                raise ValueError('cyclic task dependency')
            db.execute('INSERT INTO events(number,kind,data,created) VALUES (NULL,?,?,?)',
                       ('task_dependencies_planned', json.dumps({'task':task,'requires':names}), time.time()))
    return dependency_plan_status(db, task)


def dependency_state(db, task):
    rows = db.execute('''SELECT d.prerequisite,t.generation,c.claim,c.proposal,p.phase,i.receipt
        FROM task_dependencies d
        LEFT JOIN tasks t ON t.task=d.prerequisite
        LEFT JOIN claims c ON c.task=t.task AND c.generation=t.generation
        LEFT JOIN proposals p ON p.number=c.proposal
        LEFT JOIN integrations i ON i.number=p.number
        WHERE d.task=? ORDER BY d.prerequisite''', (task,))
    ready, blocked = [], []
    for row in rows:
        name = row['prerequisite']
        if row['phase'] != 'merged' or not row['receipt']:
            blocked.append(name)
            continue
        receipt = json.loads(row['receipt'])
        proposal = receipt['proposal']
        if (receipt['number'] != row['proposal'] or proposal['claim'] != row['claim']
                or proposal['task'] != name or proposal['generation'] != row['generation']):
            raise ValueError('integration receipt does not match its task attempt')
        ready.append({'task':name, 'generation':row['generation'], 'number':row['proposal'],
                      'merge_commit':receipt['merge_commit']})
    return ready, blocked


def dependency_plan_status(db, task):
    ready, blocked = dependency_state(db, task)
    return {'task':task, 'requires':sorted([v['task'] for v in ready] + blocked),
            'verified':ready, 'blocked':blocked}


@contextmanager
def fence(root, task):
    name = hashlib.sha256(task.encode()).hexdigest()
    with (root / ('task-' + name + '.lock')).open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AuthorityBusy('task publication or reassignment is in progress') from None
        yield


def token(db, claim):
    key = db.execute("SELECT value FROM metadata WHERE key='claim_secret'").fetchone()[0]
    return 'lit_claim_v1_' + hmac.new(bytes.fromhex(key), claim.encode(), hashlib.sha256).hexdigest()


def receipt(db, row):
    result = {key: row[key] for key in ('task', 'generation', 'claim', 'owner', 'base', 'phase', 'proposal')}
    if row['phase'] == 'active':
        result['capability'] = token(db, row['claim'])
    return result


def assign(db, args, verified_dependencies=None):
    shape = json.dumps([args.task, args.expect, args.owner, args.base], separators=(',', ':'))
    db.execute('BEGIN IMMEDIATE')
    try:
        retry = db.execute('SELECT * FROM claims WHERE request=?', (args.request,)).fetchone()
        if retry:
            if retry['request_spec'] != shape or retry['phase'] not in ('active', 'draining'):
                raise ValueError('request reused or assignment revoked')
            result = receipt(db, retry)
        else:
            if db.execute("SELECT 1 FROM proposals WHERE json_extract(spec,'$.task') IS NULL").fetchone():
                raise ValueError('use a fresh authority state; unowned proposals already exist')
            current = db.execute('SELECT generation FROM tasks WHERE task=?', (args.task,)).fetchone()
            generation = current[0] if current else 0
            if generation != args.expect:
                raise ValueError('stale task generation')
            ready, blocked = dependency_state(db, args.task)
            if blocked:
                raise DependenciesBlocked('prerequisite_merge_unverified', blocked)
            if verified_dependencies is None:
                unavailable = [r['task'] for r in ready if r['merge_commit'] != args.base]
                if unavailable:
                    raise DependenciesBlocked('dependency_base_unverified', unavailable)
            elif ready != verified_dependencies:
                raise DependenciesBlocked('prerequisite_changed', [r['task'] for r in ready])
            old = db.execute('SELECT * FROM claims WHERE task=? AND generation=?', (args.task, generation)).fetchone()
            if old and old['phase'] == 'draining':
                raise ValueError('previous attempt must retire before another reassignment')
            draining = old is not None and old['proposal'] is not None and old['phase'] != 'retired'
            if old:
                db.execute("UPDATE claims SET phase='revoked' WHERE claim=?", (old['claim'],))
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('claim_secret',?)", (secrets.token_hex(32),))
            db.execute('INSERT INTO tasks VALUES (?,?) ON CONFLICT(task) DO UPDATE SET generation=excluded.generation',
                       (args.task, generation + 1))
            claim = secrets.token_hex(16)
            db.execute('INSERT INTO claims VALUES (?,?,?,?,?,?,NULL,?,?)',
                       (claim, args.task, generation + 1, args.owner, args.base,
                        'draining' if draining else 'active', args.request, shape))
            result = receipt(db, db.execute('SELECT * FROM claims WHERE claim=?', (claim,)).fetchone())
        event = {k: v for k, v in result.items() if k != 'capability'}
        if not retry and ready:
            event['prerequisite_merges'] = ready
        db.execute('INSERT INTO events(number,kind,data,created) VALUES (NULL,?,?,?)',
                   ('task_assignment', json.dumps(event), time.time()))
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise


def binding(db, spec):
    row = db.execute('SELECT * FROM claims WHERE claim=?', (spec.get('claim'),)).fetchone()
    if not row or any(spec.get(k) != row[k] for k in ('task', 'generation', 'base')) or spec.get('agent',spec['author']) != row['owner']:
        raise ValueError('proposal does not match its task assignment')
    return row


def bind(db, spec, capabilities):
    row = binding(db, spec)
    if row['phase'] != 'active' or not hmac.compare_digest(capabilities.get(row['claim'], '').encode(), token(db, row['claim']).encode()):
        raise ValueError('assignment revoked or invalid capability')
    if row['proposal'] not in (None, spec['number']):
        raise ValueError('attempt already bound to another proposal')
    db.execute('UPDATE claims SET proposal=? WHERE claim=?', (spec['number'], row['claim']))


def retired(db, claim):
    with db:
        db.execute("UPDATE claims SET phase='retired' WHERE claim=?", (claim,))
        task = db.execute('SELECT task FROM claims WHERE claim=?', (claim,)).fetchone()[0]
        db.execute("UPDATE claims SET phase='active' WHERE task=? AND phase='draining'", (task,))


def status(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='claims'").fetchone():
        return []
    return [dict(r) for r in db.execute('SELECT task,generation,claim,owner,base,phase,proposal FROM claims ORDER BY task,generation')]
