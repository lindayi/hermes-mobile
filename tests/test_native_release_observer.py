import json
import pytest

@pytest.mark.parametrize('native_failure',[False,True])
def test_native_observer_independently_checks_listener(tmp_path,monkeypatch,capsys,native_failure):
    from deploy import observe_release as observer
    from deploy import native_controls_release as native
    from deploy.self_deploy import Paths
    calls=[];expected=tmp_path/'expected.json';expected.write_text('{}')
    stage=tmp_path/'reviewed';paths=Paths()
    monkeypatch.setattr(observer,'verify_release',lambda p,s,b:calls.append(('public',s)))
    class Probe:
        def __init__(self,source):pass
        def verify(self,s):
            calls.append(('native',s))
            if native_failure:raise RuntimeError('Native verification failed')
    monkeypatch.setattr(native,'NativeProbe',Probe)
    def observe(p,since,expected,**kw):
        kw['verify'](p,stage,True)
        return 'succeeded'
    monkeypatch.setattr(observer,'observe',observe)
    assert observer.main(['--since-ns','1','--expected',str(expected),'--unit','fixture','--native'],paths=paths)==int(native_failure)
    assert json.loads(capsys.readouterr().out)['status']==('failed' if native_failure else 'succeeded')
    assert calls==[('public',stage),('native',stage)]
