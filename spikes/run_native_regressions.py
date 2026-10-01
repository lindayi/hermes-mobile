"""Native regression launcher; no installed writes, credentials, or real model calls.
Usage (native Python 3.11):
  python spikes/run_native_regressions.py [--stage /tmp/staged] [pytest paths/args...]
pytest is pure Python and borrowed from the app venv (not installed into native).
"""
import os
from pathlib import Path
import sys
import tempfile

os.environ['HERMES_HOME'] = tempfile.mkdtemp(prefix='hermes-mobile-native-test-')
os.environ['HERMES_TEST_ISOLATION'] = os.environ['HERMES_HOME']
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
sys.dont_write_bytecode = True
for key in list(os.environ):
    if key.endswith(('_API_KEY', '_TOKEN', '_SECRET', '_PASSWORD', '_CREDENTIALS')) or key.startswith('HERMES_SESSION_'):
        os.environ.pop(key)
project = Path(__file__).resolve().parent.parent
sys.path.append(str(project / '.venv/lib/python3.12/site-packages'))
sys.path.insert(0, str(project / 'spikes'))
args = sys.argv[1:]
if args[:1] == ['--stage']:
    os.environ['NATIVE_PATCH_ROOT'] = str(Path(args[1]).resolve())
    args = args[2:]
    from test_native_fixes import load_method, AIAgent, APIServerAdapter
    AIAgent.run_conversation = load_method(AIAgent, 'run_agent.py', 'run_conversation')
    for name in ('_handle_runs', '_handle_run_approval', '_handle_capabilities', '_handle_session_messages'):
        setattr(APIServerAdapter, name, load_method(APIServerAdapter, 'gateway/platforms/api_server.py', name))
import pytest
raise SystemExit(pytest.main(['-q', '--tb=short', '-c', '/dev/null', '-p', 'no:cacheprovider', '-p', 'pytest_asyncio.plugin', *
    (args or [str(project / 'spikes/test_native_fixes.py')])]))
