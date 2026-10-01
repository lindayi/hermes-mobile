import {execFileSync} from 'node:child_process';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const repo=fileURLToPath(new URL('../../',import.meta.url));

// Generated-asset fixtures must exercise the exact supplied release. The
// developer runner's default source/frontend is the one exception: build it
// into disposable fixture storage, using the explicit no-venv interpreter.
export function generatedAssets(temporary) {
  const supplied=process.env.HERMES_FRONTEND_DIR;
  if(supplied && resolve(supplied)!==resolve(repo,'frontend'))return resolve(supplied);
  const output=join(temporary,'public'),env={...process.env};
  delete env.HERMES_FRONTEND_DIR;
  execFileSync(process.env.HERMES_TEST_PYTHON || join(repo,'.venv/bin/python'),
    ['-B','-c','from pathlib import Path; from deploy.frontend_release import build_frontend; import sys; build_frontend(Path("frontend"),Path(sys.argv[1]))',output],
    {cwd:repo,env});
  return output;
}
