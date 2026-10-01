import pytest
from test_self_deploy import deploy_fixture


@pytest.mark.parametrize('name',['native_controls_service.py','native_run_controls.py','native_maintenance.py', 'native_session_deletion.py', 'native_notifications.py'])
@pytest.mark.parametrize('change', ['add', 'modify', 'remove'])
def test_ordinary_release_rejects_native_controls_changes_before_checks_or_publication(tmp_path,name,change):
    module,paths=deploy_fixture(tmp_path)
    file=paths.source/'backend'/name
    if change != 'add': file.write_text('old native source')
    old=module.stage_release(paths.source,paths.state/'releases'/('a'*32))
    module.point_current(paths.state/'current',old)
    if change == 'remove': file.unlink()
    else: file.write_text('changed native source')
    with pytest.raises(RuntimeError,match='Unsupported dependency/native'):
        module.deploy(paths,frontend_only=True,checks=lambda _:pytest.fail('native delta reached ordinary checks'),verify=lambda *_:pytest.fail('no publication'))
    assert (paths.webroot/'index.html').read_text()=='<h1>old</h1>'
