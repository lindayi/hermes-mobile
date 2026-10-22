"""Issue #131: public Mobile scope, using only isolated synthetic state."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll
from test_native_catalog import create_native_db


ROOT = Path(__file__).resolve().parents[1]


def test_mobile_has_no_personal_feature_source_routes_assets_or_build_dependencies(tmp_path):
    from backend.app import Settings, create_app
    from deploy.autonomy_policy import (
        REQUIRED_FILES, SOURCE_BLOCKERS, SOURCE_CONTROL_PYTHON_FILES, SOURCE_FINGERPRINTS,
    )
    from deploy.frontend_release import build_frontend

    violations = []
    for directory in ('backend', 'frontend', 'design', 'docs', 'scripts', 'tests'):
        for path in (ROOT / directory).rglob('*'):
            if path.is_file() and 'finance' in path.relative_to(ROOT).as_posix().lower():
                violations.append(f'retained source: {path.relative_to(ROOT)}')
    for inventory in (REQUIRED_FILES, SOURCE_BLOCKERS, SOURCE_CONTROL_PYTHON_FILES, SOURCE_FINGERPRINTS):
        violations.extend(f'required personal source: {path}' for path in inventory if 'finance' in path)
    for filename in ('package.json', 'package-lock.json'):
        manifest = json.dumps(json.loads((ROOT / filename).read_text())).lower()
        for dependency in ('finance', 'plotly', 'esbuild'):
            if dependency in manifest:
                violations.append(f'{filename} requires {dependency}')

    home = tmp_path / 'home'
    home.mkdir()
    create_native_db(home / 'state.db')
    app = create_app(Settings(state_dir=tmp_path / 'state', profiles={'default': home},
                              bootstrap_secret=BOOTSTRAP))
    paths = set(app.openapi()['paths'])
    assert BASE + '/auth/me' in paths
    violations.extend(f'advertised personal API: {path}' for path in paths if '/finance' in path)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get(BASE + '/auth/me').status_code == 401
        client.headers['Origin'] = ORIGIN
        enroll(client)
        assert client.get(BASE + '/auth/me').json()['user']['role'] == 'owner'
        for endpoint in ('meta', 'overview', 'flow', 'transactions', 'transfers', 'investments',
                         'compensation', 'obligations', 'quotes'):
            response = client.get(BASE + '/finance/' + endpoint)
            if response.status_code != 404:
                violations.append(f'personal endpoint {endpoint}: {response.status_code}, expected 404')
        with app.state.auth.store.transaction() as db:
            db.execute('UPDATE sessions SET revoked=1')
        assert client.get(BASE + '/auth/me').status_code == 401
    assert not (home / 'finance').exists()

    output = build_frontend(ROOT / 'frontend', tmp_path / 'public')
    assert (output / 'index.html').is_file()
    assert list(output.glob('app.*.js'))
    assert list(output.glob('sw.*.js'))
    for path in output.rglob('*'):
        if not path.is_file():
            continue
        if 'finance' in path.relative_to(output).as_posix().lower():
            violations.append(f'personal generated asset: {path.relative_to(output)}')
        if path.suffix in {'.html', '.js', '.mjs', '.css', '.webmanifest'}:
            if 'finance' in path.read_text().lower():
                violations.append(f'personal generated content: {path.relative_to(output)}')
    assert not violations, '\n'.join(violations)


def test_public_asset_job_needs_only_python_not_a_javascript_bundler():
    import yaml

    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    steps = workflow['jobs']['build']['steps']
    assert not any('actions/setup-node@' in step.get('uses', '') for step in steps)
    assert not any('npm ' in step.get('run', '') for step in steps)
    assert any('deploy.release_artifact build' in step.get('run', '') for step in steps)
