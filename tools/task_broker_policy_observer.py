"""Read GitHub policy locally using the operator login; send metadata over mTLS.

No GitHub token is read, exported, logged or sent to the broker. Expiry of the
observer feed makes the broker refuse publication. Run under a trusted operator.
"""
import argparse
import json
from pathlib import Path
import ssl
import subprocess
import sys
import time
from urllib.request import HTTPSHandler, Request, build_opener
from urllib.parse import urlsplit
from github_queue_coordinator import NoRedirect


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('url','cert','key','ca','manifest'):parser.add_argument('--'+key,required=True)
    parser.add_argument('--gh',default='gh')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    config=json.loads(Path(args.manifest).read_text())
    origin=urlsplit(args.url)
    if origin.scheme!='https' or origin.username or origin.path or origin.query or origin.fragment:
        raise ValueError('expected HTTPS origin')
    context=ssl.create_default_context(cafile=args.ca);context.load_cert_chain(args.cert,args.key)
    opener=build_opener(NoRedirect(),HTTPSHandler(context=context))
    while True:
        try:
            rules={}
            for key in ('main_ruleset','freeze_ruleset','publication_ruleset'):
                rule_id=config[key]
                result=subprocess.run([args.gh,'api',f"repos/{config['repo']}/rulesets/{rule_id}"],
                    capture_output=True,timeout=20)
                if result.returncode:raise RuntimeError('policy observation unavailable')
                rule=json.loads(result.stdout)
                if 'bypass_actors' not in rule:raise RuntimeError('bypass actors not visible')
                rules[str(rule_id)]=rule
            result=subprocess.run([args.gh,'api','orgs/'+config['repo'].split('/')[0]+'/installations'],capture_output=True,timeout=20)
            if result.returncode:raise RuntimeError('worker installation observation unavailable')
            installs=json.loads(result.stdout)['installations'];workers={}
            for owner,expected in config['worker_installations'].items():
                installed=next((i for i in installs if i['id']==expected['installation_id']),None)
                if not installed or installed['app_id']!=expected['app_id'] or installed['app_slug']+'[bot]'!=owner:
                    raise RuntimeError('worker installation identity differs')
                workers[owner]=expected|{'suspended_at':installed['suspended_at']}
            payload={'rules':rules,'workers':workers,'observed_at':time.time()}
            request=Request(args.url+'/policy',json.dumps(payload).encode(),{'Content-Type':'application/json'})
            with opener.open(request,timeout=10) as response:json.load(response)
            print(json.dumps({'event':'policy_observed','rules':list(rules)}),flush=True)
        except Exception as error:
            print(json.dumps({'event':'policy_observation_failed','error':type(error).__name__}),flush=True)
            if args.once:return 1
        else:
            if args.once:return 0
        time.sleep(5)


if __name__=='__main__':sys.exit(main())
