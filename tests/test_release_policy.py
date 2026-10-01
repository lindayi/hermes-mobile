"""Pure, fail-closed routine/sensitive release classification (issue #17)."""
import importlib.util

import pytest

OID_A = '1' * 40
OID_B = '2' * 40
ZERO = '0' * 40


def module():
    assert importlib.util.find_spec('deploy.release_policy'), 'release policy is missing'
    from deploy import release_policy
    return release_policy


ROUTINE_PATHS = [
    'frontend/app.js', 'frontend/styles.css', 'frontend/index.html', 'frontend/ui.mjs',
    'frontend/session-swipe.mjs', 'frontend/icons/icon-192.png', 'frontend/icons/hermes.svg',
    'tests/test_activity_event_times.py', 'tests/browser/chat-header.spec.mjs',
    'tests/browser/activity-card.test.mjs', 'tests/browser/latest_tools_fixture.py',
    'tests/fixtures/legacy-session-stage-scope.json',
    'docs/chat-header-spec.md', 'docs/ux.md', 'docs/activity-inbox-spec.md',
]

SENSITIVE_PATHS = [
    'backend/app.py', 'hermes-plugin/mobile_delivery/__init__.py', 'patches/native-compat.patch',
    'deploy/pull_delivery.py', 'deploy/release_policy.py', 'scripts/test.py', 'spikes/probe_admission.py',
    '.github/workflows/production.yml', '.github/host-tests.json', 'AGENTS.md', 'README.md',
    'package.json', 'package-lock.json', 'requirements.lock', 'THIRD_PARTY_NOTICES.md', '.gitignore',
    'frontend/sw.js', 'frontend/api.mjs', 'frontend/webauthn.mjs', 'frontend/manifest.webmanifest',
    'frontend/auth-panel.mjs', 'frontend/push-subscription.js', 'frontend/token-store.mjs',
    'frontend/login.html', 'frontend/README.md', 'frontend/vendor/lib.js', 'frontend/app.JS',
    'frontend/icons/icon.ico',
    'tests/conftest.py', 'tests/auth_router_bridge.py', 'tests/cron_delivery_preflight_probe.py',
    'tests/browser/artifacts.mjs', 'tests/browser/generated-assets.mjs', 'tests/fixtures/data.sqlite',
    'docs/pull-delivery.md', 'docs/routine-delivery-spec.md', 'docs/self-deploy.md',
    'docs/verified-release-artifacts.md', 'docs/github-policy.md', 'docs/auth-endpoints.md',
    'docs/backup.md', 'docs/migration-publication.md', 'docs/hosted-ci-spec.md',
    'docs/git-development-spec.md', 'docs/test-hygiene.md', 'docs/development-workflow.md',
    'docs/native-run-controls-contract.md', 'docs/notification-policy.md', 'docs/security.md',
    'docs/sub/ux.md', 'docs/UX.md',
    '', '/frontend/app.js', 'frontend//app.js', 'frontend/./app.js', 'frontend/../deploy/x.py',
    'frontend\\app.js', 'frontend/.hidden.js', 'frontend/app.js/', 'frontend/ap\u00e9.js',
    'frontend/app\n.js', 'docs/' + 'a' * 300 + '.md', 'unknown/file.txt', 'newtop.md',
]


@pytest.mark.parametrize('path', ROUTINE_PATHS)
def test_routine_allowlist_is_small_and_exact(path):
    assert module().path_is_routine(path) is True


@pytest.mark.parametrize('path', SENSITIVE_PATHS)
def test_everything_else_is_sensitive_without_fallback(path):
    assert module().path_is_routine(path) is False


@pytest.mark.parametrize('value', [None, 1, b'frontend/app.js', ['frontend/app.js']])
def test_non_string_paths_are_sensitive(value):
    assert module().path_is_routine(value) is False


def change(status, path, previous=None, old='100644', new='100644'):
    return module().Change(status=status, path=path, previous_path=previous, old_mode=old, new_mode=new)


def test_routine_only_when_every_change_is_routine():
    m = module()
    changes = [change('M', 'frontend/app.js'), change('A', 'tests/test_new.py', old='000000'),
               change('D', 'docs/ux.md', new='000000')]
    result = m.classify(changes)
    assert result.risk == m.ROUTINE and result.reasons == ()


@pytest.mark.parametrize('changes,reason', [
    ([], 'empty'),
    ([('M', 'frontend/app.js'), ('M', 'backend/app.py')], 'backend/app.py'),
    ([('D', 'deploy/self_deploy.py')], 'deploy/self_deploy.py'),
    ([('A', 'requirements.lock')], 'requirements.lock'),
    ([('R', 'frontend/app.js', 'backend/app.py')], 'backend/app.py'),
    ([('R', 'backend/app.py', 'frontend/app.js')], 'backend/app.py'),
    ([('R', 'frontend/a.js', None)], 'rename'),
    ([('C', 'frontend/b.js', 'frontend/a.js')], 'status'),
    ([('T', 'frontend/app.js')], 'status'),
    ([('U', 'frontend/app.js')], 'status'),
    ([('X', 'frontend/app.js')], 'status'),
    ([('', 'frontend/app.js')], 'status'),
])
def test_sensitive_changes_and_unknown_states(changes, reason):
    m = module()
    result = m.classify([change(*item) for item in changes])
    assert result.risk == m.SENSITIVE
    assert any(reason in text for text in result.reasons)


@pytest.mark.parametrize('old,new', [('100644', '100755'), ('000000', '120000'), ('120000', '100644'),
                                     ('160000', '160000'), ('100644', None), ('100644', 'bad')])
def test_symlink_gitlink_executable_or_unknown_modes_are_sensitive(old, new):
    m = module()
    assert m.classify([m.Change('M', 'frontend/app.js', None, old, new)]).risk == m.SENSITIVE


def test_cloud_style_changes_without_modes_still_classify():
    m = module()
    assert m.classify([m.Change('M', 'frontend/app.js')]).risk == m.ROUTINE
    assert m.classify([m.Change('M', None)]).risk == m.SENSITIVE
    assert m.classify([m.Change('R', 'frontend/app.js', 7)]).risk == m.SENSITIVE


def test_truncated_or_overlarge_inventory_is_sensitive():
    m = module()
    assert m.classify([change('M', 'frontend/app.js')], truncated=True).risk == m.SENSITIVE
    many = [change('M', f'tests/test_{index}.py') for index in range(m.MAX_FILES)]
    assert m.classify(many).risk == m.ROUTINE
    assert m.classify(many + [change('M', 'tests/test_extra.py')]).risk == m.SENSITIVE


@pytest.mark.parametrize('bad', [None, 'frontend/app.js', [object()], ({'status': 'M'},),
                                 [('M', 'frontend/app.js')]])
def test_malformed_inventory_is_sensitive(bad):
    m = module()
    assert m.classify(bad).risk == m.SENSITIVE


def test_parse_git_raw_returns_exact_changes():
    m = module()
    output = (f':100644 100644 {OID_A} {OID_B} M\0frontend/app.js\0'
              f':000000 100644 {ZERO} {OID_B} A\0tests/test_new.py\0'
              f':100644 000000 {OID_A} {ZERO} D\0docs/ux.md\0'
              f':100644 100644 {OID_A} {OID_B} R087\0backend/old.py\0frontend/new.js\0')
    assert m.parse_git_raw(output) == [
        m.Change('M', 'frontend/app.js', None, '100644', '100644'),
        m.Change('A', 'tests/test_new.py', None, '000000', '100644'),
        m.Change('D', 'docs/ux.md', None, '100644', '000000'),
        m.Change('R', 'frontend/new.js', 'backend/old.py', '100644', '100644'),
    ]
    assert m.parse_git_raw('') == []


@pytest.mark.parametrize('output', [
    'frontend/app.js\0', f':100644 100644 {OID_A} {OID_B} M\0',
    f':100644 100644 {OID_A} {OID_B} M frontend/app.js\0', f':10064 100644 {OID_A} {OID_B} M\0x\0',
    f':100644 100644 {OID_A[:7]} {OID_B} M\0frontend/app.js\0', f':100644 100644 {OID_A} {OID_B} RR\0a\0b\0',
    f':100644 100644 {OID_A} {OID_B} R100\0frontend/a.js\0',
    f':100644 100644 {OID_A} {OID_B} M\0frontend/app.js',
])
def test_parse_git_raw_rejects_malformed_output(output):
    m = module()
    with pytest.raises(ValueError):
        m.parse_git_raw(output)


def test_current_docs_and_frontend_classification_is_conservative():
    """Spot-check the live tree: security/operational docs are never routine."""
    from pathlib import Path
    m = module()
    root = Path(__file__).resolve().parents[1]
    docs = {path.name for path in (root / 'docs').glob('*.md') if m.path_is_routine(f'docs/{path.name}')}
    for name in ('pull-delivery.md', 'routine-delivery-spec.md', 'self-deploy-spec.md', 'github-policy.md',
                 'guarded-delivery-spec.md', 'verified-release-artifacts.md', 'auth-endpoints.md',
                 'implementation-contract.md', 'family-jobs.md', 'family-runtime.md', 'test-hygiene.md'):
        assert name not in docs
    assert 'ux.md' in docs
