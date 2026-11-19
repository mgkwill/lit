"""Read GitHub policy with a scoped App or operator login and send metadata over mTLS.

No GitHub token is read, exported, logged or sent to the broker. Expiry of the
observer feed makes the broker refuse publication. Run under a trusted operator.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import ssl
import subprocess
import sys
import time
from urllib.request import HTTPSHandler, Request, build_opener
from urllib.parse import urlsplit
from github_app_credentials import InstallationToken
from github_queue_coordinator import GitHub, NoRedirect
from task_broker import private_file


def post_observation(url, context, payload):
    opener=build_opener(NoRedirect(),HTTPSHandler(context=context))
    try:
        request=Request(url+'/policy',payload,{'Content-Type':'application/json'})
        with opener.open(request,timeout=10) as response:
            result=json.load(response)
            return isinstance(result,dict) and result.get('accepted') is True
    except (ValueError,OSError):
        return False


def deliver(urls, context, payload):
    with ThreadPoolExecutor(max_workers=min(8,len(urls))) as pool:
        return sum(pool.map(lambda url:post_observation(url,context,payload),urls))


def gh_get(binary, path):
    result=subprocess.run([binary,'api',path],capture_output=True,timeout=20)
    if result.returncode:
        raise RuntimeError('policy observation unavailable')
    return json.loads(result.stdout)


def app_source(path, repo):
    app=json.loads(private_file(path).read_text())
    if (app['repo']!=repo or type(app['app_id']) is not int or app['app_id']<=0
            or type(app['installation_id']) is not int or app['installation_id']<=0):
        raise ValueError('observer installation binding differs')
    token=InstallationToken(app['app_id'],app['installation_id'],private_file(app['key']),repo,
        permissions={'administration':'write','organization_administration':'read'})

    def get(path):
        return GitHub('https://api.github.com',token=token(),observer_token='').api(path)

    return get


def observation(config, get):
    rules={}
    for key in ('main_ruleset','freeze_ruleset','publication_ruleset'):
        rule_id=config[key]
        rule=get(f"repos/{config['repo']}/rulesets/{rule_id}")
        if rule.get('id')!=rule_id or 'bypass_actors' not in rule:
            raise RuntimeError('ruleset identity or bypass visibility differs')
        rules[str(rule_id)]=rule
    org=config['repo'].split('/')[0]
    workers={}
    required=config['worker_installations']
    page=1
    while workers.keys()!=required.keys():
        installs=get(f'orgs/{org}/installations?per_page=100&page={page}')['installations']
        for installed in installs:
            owner=installed['app_slug']+'[bot]'
            if owner not in required:
                continue
            expected=required[owner]
            if installed['id']!=expected['installation_id'] or installed['app_id']!=expected['app_id']:
                raise RuntimeError('worker installation identity differs')
            workers[owner]=expected|{'suspended_at':installed['suspended_at']}
        if workers.keys()!=required.keys() and len(installs)<100:
            raise RuntimeError('required worker installation is missing')
        page+=1
    return {'rules':rules,'workers':workers,'observed_at':time.time()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True,action='append')
    for key in ('cert','key','ca','manifest'):parser.add_argument('--'+key,required=True)
    source=parser.add_mutually_exclusive_group()
    source.add_argument('--gh',default='gh')
    source.add_argument('--github-app',type=Path)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    config=json.loads(Path(args.manifest).read_text())
    get=app_source(args.github_app,config['repo']) if args.github_app else lambda path:gh_get(args.gh,path)
    urls=list(dict.fromkeys(args.url))
    for url in urls:
        origin=urlsplit(url)
        if (origin.scheme!='https' or not origin.hostname or origin.username is not None
                or origin.path or origin.query or origin.fragment):
            raise ValueError('expected HTTPS origin')
    context=ssl.create_default_context(cafile=args.ca);context.load_cert_chain(args.cert,args.key)
    while True:
        try:
            payload=observation(config,get)
            accepted=deliver(urls,context,json.dumps(payload).encode())
            if not accepted:
                raise RuntimeError('no broker accepted the policy observation')
            print(json.dumps({'event':'policy_observed','rules':list(payload['rules']),'accepted':accepted}),flush=True)
        except Exception as error:
            print(json.dumps({'event':'policy_observation_failed','error':type(error).__name__}),flush=True)
            if args.once:return 1
        else:
            if args.once:return 0
        time.sleep(5)


if __name__=='__main__':sys.exit(main())
