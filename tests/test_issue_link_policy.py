"""Exercise the metadata-only issue link check without GitHub writes."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/issue-link.yml'


def policy():
    assert WORKFLOW.is_file(), 'Issue-first PR policy must exist'
    return yaml.safe_load(WORKFLOW.read_text())


def test_issue_policy_has_no_checkout_or_write_permissions():
    data = policy()
    assert data['permissions'] == {'contents': 'read', 'issues': 'read', 'pull-requests': 'read'}
    steps = data['jobs']['issue-link']['steps']
    assert len(steps) == 1
    assert steps[0]['uses'].startswith('actions/github-script@')
    assert len(steps[0]['uses'].split('@')[1]) == 40
    assert 'checkout' not in WORKFLOW.read_text()
    assert '${{' not in steps[0]['with']['script']


def evaluate(body, *, state='open', is_pr=False, base='main'):
    script = policy()['jobs']['issue-link']['steps'][0]['with']['script']
    payload = {'body': body, 'state': state, 'is_pr': is_pr, 'base': base}
    harness = r'''
const payload=JSON.parse(process.argv[1]), script=JSON.parse(process.argv[2]);
const errors=[], requests=[];
const context={repo:{owner:'lindayi',repo:'hermes-mobile'},payload:{
 repository:{default_branch:'main'}, pull_request:{body:payload.body,base:{ref:payload.base}}
}};
const core={setFailed:s=>errors.push(s),info:()=>{}};
const github={rest:{issues:{get:async args=>{requests.push(args.issue_number);return {data:{state:payload.state,...(payload.is_pr?{pull_request:{}}:{})}};}}}};
(async()=>{
 await (new Function('github','context','core','return (async()=>{'+script+'})()'))(github,context,core);
 console.log(JSON.stringify({errors,requests}));
})().catch(e=>{console.error(e);process.exit(1);});
'''
    node = shutil.which('node') or '/home/lindayi/.hermes/node/bin/node'
    result = subprocess.run([node, '-e', harness, json.dumps(payload), json.dumps(script)],
                            capture_output=True, text=True, check=True, timeout=10)
    return json.loads(result.stdout)


@pytest.mark.parametrize('body', ['Closes #7', 'Fixes: #7', 'Resolves lindayi/hermes-mobile#7'])
def test_valid_closing_issue(body):
    assert evaluate(body) == {'errors': [], 'requests': [7]}


@pytest.mark.parametrize('body', ['', 'Related to #7', 'Closes other/project#7',
                                  '```\nCloses #7\n```', '<!-- Closes #7 -->',
                                  '> Closes #7', 'Closes #0'])
def test_missing_or_nonactionable_link_fails(body):
    assert evaluate(body)['errors']


def test_pr_cannot_be_used_as_issue():
    assert evaluate('Closes #7', is_pr=True)['errors']


def test_closed_issue_requires_new_actionable_issue():
    assert evaluate('Closes #7', state='closed')['errors']


def test_nonmain_pr_must_not_claim_automatic_issue_closure():
    assert evaluate('Closes #7', base='feature-base')['errors']


def test_duplicate_references_are_checked_once():
    assert evaluate('Closes #7\nFixes #7')['requests'] == [7]
