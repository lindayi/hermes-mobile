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


def test_issue_policy_uses_trusted_base_with_read_only_validation():
    data = policy()
    events = data.get('on', data.get(True))  # PyYAML's YAML 1.1 treats 'on' as True.
    assert events == {'pull_request_target': {
        'types': ['opened', 'edited', 'synchronize', 'reopened', 'ready_for_review']}}
    assert data['permissions'] == {}
    job = data['jobs']['issue-link']
    assert job['name'] != 'issue-link'  # Never confuse the base-SHA job with the head status.
    assert job['permissions'] == {'issues': 'read'}
    steps = job['steps']
    assert len(steps) == 1
    for entry in data['jobs'].values():
        assert 'environment' not in entry
        for step in entry['steps']:
            assert step['uses'] == 'actions/github-script@ed597411d8f924073f98dfc5c65a23a2325f34cd'
            assert '${{' not in step['with']['script']
            assert 'secrets.' not in json.dumps(step)
            assert 'run' not in step
    assert 'checkout' not in WORKFLOW.read_text()


def evaluate(body, *, state='open', is_pr=False, base='main', job='issue-link',
             validation_result='success', current=None, issue_error=False,
             pull_error=False, status_error=False):
    jobs = policy()['jobs']
    assert job in jobs, f'Missing trusted metadata job: {job}'
    script = jobs[job]['steps'][0]['with']['script']
    payload = dict(body=body, state=state, is_pr=is_pr, base=base, current=current,
                   validation_result=validation_result, issue_error=issue_error,
                   pull_error=pull_error, status_error=status_error)
    harness = r'''
const payload=JSON.parse(process.argv[1]), script=JSON.parse(process.argv[2]);
const errors=[], requests=[], statuses=[], pull_requests=[];
const pr={number:12,body:payload.body,base:{ref:payload.base},state:'open',
 head:{sha:'a'.repeat(40),repo:{full_name:'fork-owner/hermes-mobile'}}};
const context={repo:{owner:'lindayi',repo:'hermes-mobile'},sha:'b'.repeat(40),
 runId:12345,serverUrl:'https://github.com',payload:{
 repository:{default_branch:'main'},pull_request:pr}};
const core={setFailed:s=>errors.push(String(s)),info:()=>{}};
const github={rest:{
 issues:{get:async args=>{
   requests.push(args);
   if(payload.issue_error) throw new Error('Issue API unavailable');
   return {data:{state:payload.state,...(payload.is_pr?{pull_request:{}}:{})}};
 }},
 pulls:{get:async args=>{
   pull_requests.push(args);
   if(payload.pull_error) throw new Error('PR API unavailable');
   return {data:{...JSON.parse(JSON.stringify(pr)),...payload.current}};
 }},
 repos:{createCommitStatus:async args=>{
   if(payload.status_error) throw new Error('Status API unavailable');
   statuses.push(args);return {data:{}};
 }}
}};
process.env.VALIDATION_RESULT=payload.validation_result;
(async()=>{
 // github-script catches a thrown script/API error and calls core.setFailed.
 let thrown=null;
 try {
   await (new Function('github','context','core','return (async()=>{'+script+'})()'))(github,context,core);
 } catch(e) {thrown=e.message;core.setFailed(e.message);}
 console.log(JSON.stringify({errors,requests,statuses,pull_requests,thrown}));
})().catch(e=>{console.error(e);process.exit(1);});
'''
    node = shutil.which('node') or '/home/lindayi/.hermes/node/bin/node'
    result = subprocess.run([node, '-e', harness, json.dumps(payload), json.dumps(script)],
                            capture_output=True, text=True, check=True, timeout=10)
    return json.loads(result.stdout)


@pytest.mark.parametrize('body', [
    'Closes #7', 'Fixes: #7', 'Resolves lindayi/hermes-mobile#7',
    'Closes LINDAYI/HERMES-MOBILE#7', '- Closes #7',
    '`Closes #8`\nCloses #7', '``example ` Closes #8``\nCloses #7',
    '```Closes #8```\nCloses #7',
    '~~~text\nCloses #8\n~~~\nCloses #7',
    '````text\n```\nCloses #8\n`````\nCloses #7',
    '    Closes #8\n\nCloses #7',
    '<!-- Closes #8 -->\n> Closes #9\n\nCloses #7',
    'Closes #7\n\n~~~\nCloses #8',
    "Closes #7\n${{ secrets.ANYTHING }}\n');process.exit(19);//",
])
def test_valid_closing_issue(body):
    result = evaluate(body)
    assert result['errors'] == []
    assert result['requests'] == [{'owner': 'lindayi', 'repo': 'hermes-mobile', 'issue_number': 7}]
    assert result['statuses'] == []  # Validation never writes to GitHub.


@pytest.mark.parametrize('body', ['', 'Related to #7', 'Closes other/project#7',
                                  '```\nCloses #7\n```', '<!-- Closes #7 -->',
                                  '> Closes #7', 'Closes #0'])
def test_missing_or_nonactionable_link_fails(body):
    assert evaluate(body)['errors']


@pytest.mark.parametrize('body', [
    '`Closes #7`', '``Closes #7``', '``example ` Closes #7``',
    '`example\nCloses #7`', 'Closes `example` #7',
])
def test_inline_code_cannot_supply_a_closing_reference(body):
    result = evaluate(body)
    assert result['errors']
    assert result['requests'] == []


@pytest.mark.parametrize('body', [
    '~~~text\nCloses #7\n~~~', '~~~\nCloses #7',
    '```text\nCloses #7', '````text\n```\nCloses #7\n````',
    '~~~\n```\nCloses #7\n~~~',
    '    Closes #7', '\tCloses #7', ' \tCloses #7',
    '   ~~~text\nCloses #7\n   ~~~',
    '- ~~~\n  Closes #7\n  ~~~', '-  ~~~\n   Closes #7\n   ~~~',
    '~~~\n- ~~~\nCloses #7\n~~~',
    '~~~\n~~~ not a closing fence\nCloses #7\n~~~',
    '~~~\n<!-- example -->~~~\nCloses #7\n~~~',
    '~~~\n<!--\n~~~\n-->\n~~~\nCloses #7\n~~~',
])
def test_code_blocks_cannot_supply_a_closing_reference(body):
    result = evaluate(body)
    assert result['errors']
    assert result['requests'] == []


def test_pr_cannot_be_used_as_issue():
    assert evaluate('Closes #7', is_pr=True)['errors']


def test_closed_issue_requires_new_actionable_issue():
    assert evaluate('Closes #7', state='closed')['errors']


def test_nonmain_pr_must_not_claim_automatic_issue_closure():
    assert evaluate('Closes #7', base='feature-base')['errors']


def test_duplicate_references_are_checked_once():
    assert evaluate('Closes #7\nFixes #7')['requests'] == [
        {'owner': 'lindayi', 'repo': 'hermes-mobile', 'issue_number': 7}]


def test_pending_status_invalidates_previous_result_on_exact_pr_head():
    result = evaluate('Closes #7', job='issue-link-pending')
    assert result['errors'] == []
    assert result['statuses'] == [{
        'owner': 'lindayi', 'repo': 'hermes-mobile', 'sha': 'a' * 40,
        'context': 'issue-link', 'state': 'pending',
        'description': 'Validating issue-closing metadata.',
        'target_url': 'https://github.com/lindayi/hermes-mobile/actions/runs/12345',
    }]
    assert result['requests'] == result['pull_requests'] == []
    data = policy()
    assert data['jobs']['issue-link-pending']['permissions'] == {'statuses': 'write'}
    assert data['jobs']['issue-link']['needs'] == 'issue-link-pending'
    assert data['concurrency']['cancel-in-progress'] is False


@pytest.mark.parametrize('verdict', ['success', 'failure', 'cancelled', 'skipped', ''])
def test_validation_result_is_published_as_exact_head_required_status(verdict):
    result = evaluate('Closes #7', job='issue-link-result', validation_result=verdict)
    assert result['statuses'] == [{
        'owner': 'lindayi', 'repo': 'hermes-mobile', 'sha': 'a' * 40,
        'context': 'issue-link', 'state': 'success' if verdict == 'success' else 'failure',
        'description': 'Issue-closing policy passed.' if verdict == 'success'
                       else 'Issue-closing policy failed; inspect this run.',
        'target_url': 'https://github.com/lindayi/hermes-mobile/actions/runs/12345',
    }]
    job = policy()['jobs']['issue-link-result']
    assert job['needs'] == ['issue-link-pending', 'issue-link']
    assert job['if'] == '${{ always() }}'
    assert job['permissions'] == {'statuses': 'write', 'pull-requests': 'read'}
    assert job['steps'][0]['env'] == {'VALIDATION_RESULT': '${{ needs.issue-link.result }}'}


@pytest.mark.parametrize('current', [
    {'head': {'sha': 'c' * 40}}, {'body': 'Removed closing reference'},
    {'base': {'ref': 'feature-base'}}, {'state': 'closed'},
])
def test_superseded_metadata_cannot_publish_a_success(current):
    result = evaluate('Closes #7', job='issue-link-result', current=current)
    assert result['statuses'][0]['state'] == 'failure'
    assert result['statuses'][0]['sha'] == 'a' * 40
    assert result['pull_requests'] == [
        {'owner': 'lindayi', 'repo': 'hermes-mobile', 'pull_number': 12}]


@pytest.mark.parametrize('body', [None, 'Closes #9007199254740993',
                                  '\n'.join(f'Closes #{i}' for i in range(1, 12))])
def test_invalid_or_excessive_issue_numbers_fail_without_api_calls(body):
    result = evaluate(body)
    assert result['errors']
    assert result['requests'] == result['statuses'] == []


def test_ten_unique_open_issues_remain_allowed():
    result = evaluate('\n'.join(f'Closes #{i}' for i in range(1, 11)))
    assert result['errors'] == []
    assert [request['issue_number'] for request in result['requests']] == list(range(1, 11))


@pytest.mark.parametrize('job,options', [
    ('issue-link', {'issue_error': True}),
    ('issue-link-pending', {'status_error': True}),
    ('issue-link-result', {'pull_error': True}),
    ('issue-link-result', {'status_error': True}),
])
def test_api_errors_never_produce_a_success(job, options):
    result = evaluate('Closes #7', job=job, **options)
    assert result['errors']
    assert result['thrown'] is not None
    assert result['statuses'] == []


@pytest.mark.parametrize('body', ['Closes #7', '', '`Closes #7`'])
def test_real_validator_verdict_drives_publication(body):
    pending = evaluate(body, job='issue-link-pending')
    validation = evaluate(body)
    verdict = 'failure' if validation['errors'] else 'success'
    published = evaluate(body, job='issue-link-result', validation_result=verdict)
    assert [status['state'] for status in pending['statuses'] + published['statuses']] == [
        'pending', verdict]
    assert all(status['sha'] == 'a' * 40
               for status in pending['statuses'] + published['statuses'])
