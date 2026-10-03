# Session opening scroll acceptance

Issue #73 addresses the initial transcript position when opening a session from
the Sessions list. The initial history request already asks for the latest page;
the requirement is to finish opening at the bottom of that page plus any current
run replay or terminal output, rather than at the earlier user input.

The opening-follow intent lasts only through initial history/run restoration and
the first render settling. If the reader intentionally scrolls or selects text
before delayed restoration finishes, restoration must preserve that choice.
Later live updates follow only a reader who was already following the tail and
has not selected text. Scroll only the transcript; do not move the document or
focus the composer. Async work from an old route must not change the new
session's scroll position.

Preserve latest-page selection, older-page prepend anchoring, replay batching,
tool collapse state, public activity chronology, background-result placement,
ordinary live tail-following and exactly-once run behavior. Do not derive scroll
intent by measuring layout once per replay event.

Acceptance uses real generated frontend assets, a synthetic loopback API and
Chromium navigation by clicking a Sessions-list row at a narrow viewport. Long
synthetic history/replay fixtures exercise active public progress and completed
terminal output. These tests do not establish physical Safari keyboard or
browser-chrome behavior.

Baseline: `114897bca0f8d2d8be13752e50ccc181c189fe8c`.

Observed RED before the frontend change:

```sh
HERMES_TEST_PYTHON="${HERMES_TEST_PYTHON:-$PWD/.venv/bin/python}" \
HERMES_TEST_NODE="${HERMES_TEST_NODE:-$(command -v node)}" \
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-${XDG_CACHE_HOME:-$HOME/.cache}/hermes-mobile-playwright}" \
PLAYWRIGHT_SKIP_BROWSER_GC=1 \
python3 scripts/test.py browser -- tests/browser/session-opening.spec.mjs
```

The generated-assets Chromium regression reproduced the bug in both cases: the
active replay ended 11,845 px above the transcript bottom, and completed output
ended 14,319 px above it. Existing session-opening input/navigation cases passed.
