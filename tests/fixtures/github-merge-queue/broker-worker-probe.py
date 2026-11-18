"""Sandbox qualification worker; receive the short-lived GitHub token on stdin."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO='100latent/lit-merge-queue-sandbox'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('action',choices=('publish','challenge','repropose','reopen','close','revoked','reader'))
parser.add_argument('--host',required=True,choices=('host-a','host-e','host-f'))
parser.add_argument('--base',required=True)
parser.add_argument('--branch',required=True)
parser.add_argument('--run',required=True)
parser.add_argument('--pr',type=int)
args=parser.parse_args()
if (socket.gethostname().split('.')[0]!=args.host or not re.fullmatch(r'[0-9a-f]{40}',args.base)
        or not re.fullmatch(r'agents/broker-pilot/[a-zA-Z0-9-]+/ai-0[167]',args.branch)
        or not re.fullmatch(r'[a-zA-Z0-9-]+',args.run)):
    parser.error('invalid host or pilot identity')
token=sys.stdin.readline().strip()
if not token.startswith('ghs_'):parser.error('expected installation token on stdin')

def api(path,payload=None,method=None):
    request=Request('https://api.github.com/'+path,None if payload is None else json.dumps(payload).encode(),
        {'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','Content-Type':'application/json'},method=method)
    with urlopen(request,timeout=30) as response:return json.load(response)

if args.action in ('revoked','reader'):
    checks=[]
    def denied(path,payload=None,method=None):
        try:
            api(path,payload,method)
        except HTTPError as error:
            with error:body=json.load(error);code=error.code
            assert code in (401,403),body
            checks.append({'route':path,'http':code,'message':body.get('message')})
            return
        raise AssertionError('credential unexpectedly retained write access')
    if args.action=='revoked':
        denied('installation/repositories')
        denied(f'repos/{REPO}/git/refs',{'ref':'refs/heads/agents/revocation-probe/'+args.host,'sha':args.base})
        denied(f'repos/{REPO}/pulls/106/merge-async',{'sha':args.base,'merge_action':'merge_queue'},'PUT')
    else:
        repos=api('installation/repositories')
        assert repos['total_count']==1 and repos['repositories'][0]['full_name']==REPO
        api(f'repos/{REPO}/git/commits/{args.base}')
        pr=api(f'repos/{REPO}/pulls/106')
        try:
            blocked=api('graphql',{'query':"""mutation($pr:ID!,$head:GitObjectID!){enqueuePullRequest(input:{pullRequestId:$pr,expectedHeadOid:$head}){mergeQueueEntry{id}}}""",
                'variables':{'pr':pr['node_id'],'head':pr['head']['sha']}})
        except HTTPError as error:
            with error:body=json.load(error);code=error.code
            assert code in (401,403),body
            checks.append({'route':'graphql.enqueuePullRequest','http':code,'message':body.get('message')})
        else:
            assert any(e.get('type')=='FORBIDDEN' for e in blocked.get('errors',[])),blocked
            checks.append({'route':'graphql.enqueuePullRequest','http':200,'errors':blocked['errors']})
        denied(f'repos/{REPO}/git/refs',{'ref':'refs/heads/agents/broker/attempt/read-probe-'+args.host,'sha':args.base})
        denied(f'repos/{REPO}/pulls/106/merge-async',{'sha':args.base,'merge_action':'merge_queue'},'PUT')
    print(json.dumps({'host':args.host,'credential':args.action,'checks':checks}))
    sys.exit(0)
repos=api('installation/repositories')
assert repos['total_count']==1 and repos['repositories'][0]['full_name']==REPO
if args.action in ('reopen','close','revoked','reader'):
    assert args.pr
    old=api(f'repos/{REPO}/pulls/{args.pr}')
    assert old['head']['ref']==args.branch and old['user']['login']=='100latent-lit-pilot-'+args.host+'[bot]'
    pr=api(f'repos/{REPO}/pulls/{args.pr}',{'state':'open' if args.action=='reopen' else 'closed'},method='PATCH')
    print(json.dumps({'number':pr['number'],'state':pr['state'],'head':pr['head']['sha']}))
    sys.exit(0)
if args.action=='repropose':
    pr=api(f'repos/{REPO}/pulls',{'head':args.branch,'base':'main',
        'title':'Qualify recovered broker attempt from '+args.host,
        'body':'Fresh attempt after fail-closed retirement of the original qualification PR.'})
    print(json.dumps({'host':args.host,'number':pr['number'],'node':pr['node_id'],'head':pr['head']['sha'],
                      'branch':args.branch,'author':pr['user']['login'],'url':pr['html_url']}))
    sys.exit(0)
with tempfile.TemporaryDirectory(prefix='lit-broker-worker-') as scratch:
    root=Path(scratch);askpass=root/'askpass'
    askpass.write_text('#!/bin/sh\nprintf "%s\\n" "$LIT_WORKER_TOKEN"\n');askpass.chmod(0o700)
    env={'PATH':os.environ['PATH'],'HOME':scratch,'LC_ALL':'C','GIT_CONFIG_NOSYSTEM':'1',
         'GIT_CONFIG_GLOBAL':os.devnull,'GIT_ASKPASS':str(askpass),'GIT_TERMINAL_PROMPT':'0',
         'LIT_WORKER_TOKEN':token,'GIT_AUTHOR_NAME':args.host,'GIT_COMMITTER_NAME':args.host,
         'GIT_AUTHOR_EMAIL':args.host+'@example.invalid','GIT_COMMITTER_EMAIL':args.host+'@example.invalid'}
    def git(*command,expected=True):
        result=subprocess.run(['git','-c','credential.helper=','-c','credential.username=x-access-token',*command],
            cwd=root,env=env,text=True,capture_output=True,timeout=90)
        if expected and result.returncode:raise RuntimeError('native Git operation failed')
        return result
    remote='https://github.com/'+REPO+'.git'
    base=api(f'repos/{REPO}/git/ref/heads/main')['object']['sha'] if args.action=='challenge' else args.base
    git('init','-q');git('fetch','--depth=1',remote,base);git('checkout','-q','--detach',base)
    path=root/'qualification'/'broker-pilot'/args.run/(args.host+'.txt')
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text('Authenticated task broker pilot from '+args.host+'\n')
    git('add',str(path.relative_to(root)));git('commit','-q','-m','Qualify task broker worker '+args.host)
    head=git('rev-parse','HEAD').stdout.strip()
    if args.action=='publish':
        git('push',remote,'HEAD:refs/heads/'+args.branch)
        pr=api(f'repos/{REPO}/pulls',{'head':args.branch,'base':'main',
            'title':'Qualify broker-owned task attempt from '+args.host,
            'body':'Synthetic authenticated task ownership and reassignment qualification.'})
        assert pr['head']['sha']==head
        print(json.dumps({'host':args.host,'number':pr['number'],'node':pr['node_id'],'head':head,
                          'branch':args.branch,'author':pr['user']['login'],'url':pr['html_url']}))
    else:
        assert args.pr
        pr=api(f'repos/{REPO}/pulls/{args.pr}')
        before=api(f'repos/{REPO}/git/ref/heads/main')['object']['sha']
        denied=git('push',remote,'HEAD:refs/heads/main',expected=False)
        assert denied.returncode and re.search('GH013|GH006',denied.stderr),'direct main update was not ruleset-denied'
        result=api('graphql',{'query':'''mutation($pr:ID!,$head:GitObjectID!){
          enqueuePullRequest(input:{pullRequestId:$pr,expectedHeadOid:$head}){mergeQueueEntry{id}}}''',
          'variables':{'pr':pr['node_id'],'head':pr['head']['sha']}})
        assert result.get('errors'),'worker bypassed the broker admission rule'
        def attempt(path,payload,method='PUT'):
            try:
                request=Request('https://api.github.com/'+path,json.dumps(payload).encode(),
                    {'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','Content-Type':'application/json'},method=method)
                with urlopen(request,timeout=30) as response:return response.status,json.load(response)
            except HTTPError as error:
                with error:return error.code,json.load(error)
        classic_code,classic=attempt(f'repos/{REPO}/pulls/{args.pr}/merge',
            {'sha':pr['head']['sha'],'merge_method':'merge'})
        assert classic_code in (403,405,409,422) and not classic.get('merged'),'worker bypassed queue using REST merge'
        asynchronous=[]
        for merge_action in ('direct_merge','merge_queue'):
            payload={'sha':pr['head']['sha'],'merge_action':merge_action,'bypass_rules':True}
            if merge_action=='direct_merge':payload['merge_method']='merge'
            code,reply=attempt(f'repos/{REPO}/pulls/{args.pr}/merge-async',payload)
            end=time.monotonic()+60
            while code in (200,202) and reply.get('status')=='pending' and time.monotonic()<end:
                identifier=reply['details']['uuid']
                time.sleep(.5)
                reply=api(f'repos/{REPO}/pulls/{args.pr}/merge-async/{identifier}')
            assert code in (403,405,409,422) or reply.get('status')=='failed',reply
            asynchronous.append({'action':merge_action,'initial_http':code,'result':reply})
        automatic=api('graphql',{'query':"""mutation($pr:ID!){enablePullRequestAutoMerge(input:{pullRequestId:$pr,mergeMethod:MERGE}){pullRequest{id}}}""",
                               'variables':{'pr':pr['node_id']}})
        if not automatic.get('errors'):
            disabled=api('graphql',{'query':"""mutation($pr:ID!){disablePullRequestAutoMerge(input:{pullRequestId:$pr}){pullRequest{id}}}""",
                                   'variables':{'pr':pr['node_id']}})
            assert not disabled.get('errors')
        current=api('graphql',{'query':"""query($pr:ID!){node(id:$pr){... on PullRequest{merged mergeQueueEntry{id}}}}""",
                               'variables':{'pr':pr['node_id']}})['data']['node']
        assert not current['merged'] and current['mergeQueueEntry'] is None
        assert api(f'repos/{REPO}/git/ref/heads/main')['object']['sha']==before
        print(json.dumps({'host':args.host,'number':args.pr,'direct_push':'ruleset_denied',
                          'direct_enqueue_errors':result['errors'],'rest_merge':{'http':classic_code,'result':classic},
                          'async_merges':asynchronous,'auto_merge':automatic,'main_unchanged':before}))
