"""Photo persistence and vision behavior against the pinned native runtime."""
import os
from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
NATIVE_PYTHON = '/usr/local/lib/hermes-agent/venv/bin/python'


def run_native_probe(tmp_path, code):
    home = tmp_path / 'isolated-home'
    home.mkdir()
    environment = {
        'HOME': str(home),
        'HERMES_HOME': str(home),
        'LANG': 'C.UTF-8',
        'PATH': '/usr/local/bin:/usr/bin:/bin',
        'PYTHONPATH': f'{ROOT}:/usr/local/lib/hermes-agent',
    }
    result = subprocess.run(
        [NATIVE_PYTHON, '-c', textwrap.dedent(code)],
        env=environment,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_transcript_and_json_snapshot_omit_image_bytes(tmp_path):
    run_native_probe(tmp_path, '''
        import json
        from datetime import datetime
        from pathlib import Path
        import os
        from hermes_state import SessionDB
        from run_agent import AIAgent

        home = Path(os.environ['HERMES_HOME'])
        db = SessionDB(home / 'state.db')
        db.create_session('photo-fixture', 'api_server')
        agent = AIAgent.__new__(AIAgent)
        agent._session_db = db
        agent._session_db_created = True
        agent._last_flushed_db_idx = 0
        agent._flushed_db_message_ids = set()
        agent._flushed_db_message_session_id = None
        agent._db_flush_scan_prefix = None
        agent._persist_disabled = False
        agent.session_id = 'photo-fixture'
        marker = 'SYNTHETIC_IMAGE_PAYLOAD'
        data_url = 'data:image/png;base64,' + marker
        message = {
            'role': 'user',
            'content': [
                {'type': 'text', 'text': 'Describe this synthetic photo.'},
                {'type': 'image_url', 'image_url': {'url': data_url}},
            ],
        }
        agent._flush_messages_to_session_db([message], conversation_history=[])
        row = db._conn.execute(
            'SELECT content, api_content FROM messages WHERE session_id=?',
            ('photo-fixture',),
        ).fetchone()
        assert row is not None
        assert '[screenshot]' in row['content']
        assert marker not in str(tuple(row))
        assert row['api_content'] is None

        agent._session_json_enabled = True
        agent.logs_dir = home / 'sessions'
        agent.logs_dir.mkdir()
        agent.model = 'synthetic-model'
        agent.base_url = 'http://127.0.0.1'
        agent.platform = 'api_server'
        agent.session_start = datetime.now()
        agent._cached_system_prompt = ''
        agent.tools = []
        agent.verbose_logging = False
        agent._save_session_log([message])
        snapshot = (agent.logs_dir / 'session_photo-fixture.json').read_text()
        assert marker not in snapshot
        assert 'data:image/' not in snapshot
        assert '[photo attachment omitted after processing]' in snapshot
    ''')


def test_native_vision_failure_is_reported_without_synthetic_description(tmp_path):
    run_native_probe(tmp_path, '''
        import asyncio
        import sys
        import types
        from run_agent import AIAgent

        async def unavailable(**kwargs):
            raise RuntimeError('synthetic provider unavailable')

        sys.modules['tools.vision_tools'] = types.SimpleNamespace(
            vision_analyze_tool=unavailable,
        )
        agent = AIAgent.__new__(AIAgent)
        agent._anthropic_image_fallback_cache = {}
        agent._materialize_data_url_for_vision = lambda source: (source, None)
        try:
            agent._describe_image_for_anthropic_fallback(
                'data:image/png;base64,SYNTHETIC_IMAGE_PAYLOAD',
                'user',
            )
        except RuntimeError as error:
            assert str(error) == 'The configured vision analyzer could not process the attached image.'
        else:
            raise AssertionError('Vision failure was converted into a text-only result')
    ''')
