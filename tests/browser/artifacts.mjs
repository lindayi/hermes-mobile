import {resolve, sep} from 'node:path';
import {pathToFileURL} from 'node:url';

// Empty means the directory (including its trailing slash); otherwise accept
// only a single portable filename, never a path or URL. Callers create the directory.
export function artifactURL(relative = '') {
  if (typeof relative !== 'string' || (relative !== '' && !/^[a-zA-Z0-9][a-zA-Z0-9._-]*(?![\s\S])/.test(relative))) {
    throw new TypeError('Invalid artifact filename: expected a single portable filename');
  }
  const directory = process.env.HERMES_TEST_ARTIFACT_DIR;
  const base = directory
    ? pathToFileURL(resolve(directory) + sep)
    : new URL('./artifacts/', import.meta.url);
  return new URL(relative, base);
}
