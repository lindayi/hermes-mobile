"""Every shipped public module remains available in the versioned offline shell."""
import ast
from pathlib import Path
import re

from deploy.frontend_release import build_frontend


def test_generated_public_modules_are_all_precached_including_swipe(tmp_path):
    root = Path(__file__).resolve().parents[1]
    public = build_frontend(root / 'frontend', tmp_path / 'public')
    worker = next(public.glob('sw.*.js')).read_text()
    assets = set(ast.literal_eval(re.search(r'const ASSETS=(\[.*?\]);', worker).group(1)))
    modules = {'/hermes/' + path.name for path in public.glob('*.mjs')}
    assert any('/session-swipe.' in name for name in modules)
    assert modules <= assets
    assert not any('app-api' in name for name in assets)
