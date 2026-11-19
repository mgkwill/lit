"""Live broker qualification client. No GitHub writer credentials or claim logging.

Each input operation names a distinct preinstalled mTLS profile. Run this on the
named host; scheduler credentials and the CA signing key stay off worker hosts.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from http.client import HTTPException
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def run(operation, root, url, action):
    profile = root / operation['id']
    context = ssl.create_default_context(cafile=str(root / 'ca.crt'))
    context.load_cert_chain(str(profile / 'client.crt'), str(profile / 'client.key'))
    opener = build_opener(NoRedirect(), HTTPSHandler(context=context))
    attempts = 0
    busy = 0
    transport_retries = 0
    publication_posts = 0
    polls = 0
    accepted = []
    started = time.time()

    def call(route, payload=None, retry=True):
        nonlocal attempts, busy, transport_retries, publication_posts, polls
        deadline = time.monotonic() + 1800
        while True:
            attempts += 1
            if route == 'propose': publication_posts += 1
            if route.startswith(('publication?claim=', 'registration?claim=')): polls += 1
            request = Request(url + '/' + route,
                              None if payload is None else json.dumps(payload).encode(),
                              {'Content-Type': 'application/json'})
            try:
                with opener.open(request, timeout=40) as response:
                    code, body = response.status, json.load(response)
                if code == 202:
                    if not accepted:
                        accepted.append({'operation':body['operation'],'state':body['state'],
                                         'at':time.time(),'status_path':body['status_path']})
                    # Poll an accepted operation; never resubmit a remote write.
                    route = body['status_path'].lstrip('/')
                    assert route.startswith(('publication?claim=', 'registration?claim=')) and '/' not in route
                    payload = None
                    if time.monotonic() >= deadline: raise TimeoutError('broker operation pending')
                    time.sleep(max(.1,min(30,body.get('poll_after_seconds',1))))
                    continue
                return code, body
            except HTTPError as error:
                with error:
                    code, body = error.code, json.load(error)
                if retry and code in (429,503) and body.get('retryable') is True and time.monotonic() < deadline:
                    busy += 1
                    time.sleep(min(3, .2 + busy * .08))
                    continue
                return code, body
            except (URLError, HTTPException, TimeoutError, ConnectionError) as error:
                # Admission is idempotent by claim+digest. If its reply was lost,
                # identical submission joins the job; it never repeats an unknown
                # provider mutation. Do not suppress identity/certificate errors.
                reason=getattr(error,'reason',error)
                if isinstance(reason,ssl.SSLCertVerificationError) or not retry or time.monotonic()>=deadline:
                    raise
                transport_retries += 1
                time.sleep(min(3,.2+transport_retries*.1))

    code, claims = call('claims')
    assert code == 200
    assert all(c['owner'] == operation['owner'] for c in claims['claims'])
    matches = [c for c in claims['claims'] if c['task'] == operation['task']
               and c['generation'] == operation.get('generation', 1)]
    assert len(matches) == 1, 'missing scoped assignment'
    claim = matches[0]
    record = {'id': operation['id'], 'owner': operation['owner'], 'task': claim['task'],
              'generation': claim['generation'], 'host': socket.gethostname(),
              'started': started, 'action': action}
    if action == 'publish':
        assert claim['phase'] == 'active' and 'capability' in claim
        body = {k: claim[k] for k in ('task', 'generation', 'claim', 'base', 'capability')}
        body.update(request=operation['request'], files=operation['files'])
        code, receipt = call('propose', body)
        assert code == 200, 'publication rejected or unresolved'
        assert receipt['agent'] == operation['owner']
        code, replay = call('propose', body)
        assert code == 200 and replay == receipt, 'publication replay changed receipt'
        record['receipt'] = receipt
        record['publication_replay_identical'] = True
    elif action == 'register':
        assert claim['phase'] == 'active' and 'capability' in claim
        code, reply = call('register', {'proposal': operation['receipt'],
                                       'capability': claim['capability']})
        assert code == 200 and reply['registered'] == operation['receipt']['number']
        record['registered'] = reply['registered']
    elif action == 'stale':
        assert claim['phase'] in ('revoked', 'retired') and 'capability' not in claim
        # The controller supplies the old capability only over stdin, never argv.
        body = {k: claim[k] for k in ('task', 'generation', 'claim', 'base')}
        body.update(capability=operation['old_capability'], request=operation['request'],
                    files=operation['files'])
        code, denied = call('propose', body)
        assert code == 409, 'revoked attempt could still publish'
        assert denied.get('retryable') is False
        code, denied = call('register', {'proposal': operation['receipt'],
                                       'capability': operation['old_capability']})
        assert code == 409, 'revoked attempt could still register'
        assert denied.get('retryable') is False
        record['stale_publication_and_registration_denied'] = True
    elif action == 'permissions':
        assert call('status', retry=False)[0] == 403
        assert call('assign', {}, retry=False)[0] == 403
        assert call('register', {'proposal': operation['peer_receipt'],
                                 'capability': 'not-a-capability'}, retry=False)[0] == 403
        code, credential = call('repository-token')
        assert code == 200 and credential['permissions'] == {'contents': 'read', 'pull_requests': 'read'}
        repo = credential['repository']
        token = credential['token']
        # GitHub uses the public trust store, not the broker's private CA.
        github_opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        def github(path, body=None):
            request = Request('https://api.github.com/' + path,
                              None if body is None else json.dumps(body).encode(),
                              {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                               'Accept': 'application/vnd.github+json'})
            try:
                with github_opener.open(request, timeout=30) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                with error: return error.code, json.load(error)
        assert github('repos/' + repo)[0] == 200
        pr = github('repos/' + repo + '/pulls/' + str(operation['peer_receipt']['number']))[1]
        code, denied = github('graphql', {'query': 'mutation($pr:ID!,$head:GitObjectID!){enqueuePullRequest(input:{pullRequestId:$pr,expectedHeadOid:$head}){mergeQueueEntry{id}}}',
                                         'variables': {'pr': pr['node_id'], 'head': pr['head']['sha']}})
        assert code == 403 or any(e.get('type') == 'FORBIDDEN' for e in denied.get('errors', []))
        assert github('repos/' + repo + '/git/refs', {'ref': 'refs/heads/agents/broker/attempt/denied-' + operation['id'],
                                                    'sha': claim['base']})[0] == 403
        record['admin_peer_and_direct_github_writes_denied'] = True
        record['read_permissions'] = credential['permissions']
    else:
        raise ValueError('unknown action')
    record.update(finished=time.time(), requests=attempts, busy_retries=busy,
                  transport_retries=transport_retries,publication_posts=publication_posts,
                  status_polls=polls,admission=accepted[0] if accepted else None)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, choices=('host-a', 'host-e', 'host-f'))
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--url', default='https://192.0.2.10:19443')
    parser.add_argument('--concurrency',type=int,default=10)
    parser.add_argument('action', choices=('publish', 'register', 'stale', 'permissions'))
    args = parser.parse_args()
    assert socket.gethostname().split('.')[0] == args.host
    operations = json.load(sys.stdin)
    assert 0 < len(operations) <= 40
    assert 1 <= args.concurrency <= 40
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(run, op, args.profiles, args.url, args.action) for op in operations]
        for future in futures:
            # Emit each completed receipt even if a later operation fails.
            print(json.dumps(future.result()), flush=True)


if __name__ == '__main__':
    main()
