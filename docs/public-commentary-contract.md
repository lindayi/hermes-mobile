# Saved public commentary acceptance and contract

Acceptance: a saved native assistant row with empty content, tool calls, and
adapter-shaped `codex_message_items` exposes only explicit public commentary,
before its tools. Analysis/reasoning/final/refusal/private sentinels never appear.
The original raw row remains one pagination coordinate.

API (integration contract):
- `public_commentary_items(value)` accepts the sidecar JSON string or parsed list,
  not a whole native row. Returns presentation dictionaries with `role: assistant`,
  `channel: commentary`, sanitized `content`, and original zero-based `item_index`.
- `project_public_commentary(native_row_id, value)` adds stable `id` values.
- Attach the result as `normalized_message.public_commentary`; render this list
  BEFORE that row's tools. Do not insert extra native rows or alter offset counts.
- Only exact `type=message`, `phase=commentary`, and string `output_text` parts
  qualify. No fallback to content, reasoning_content, or provider metadata.

## Safety and limits

- Installed `run_agent.py:6575–6622` confirms the structured-message boundary;
  `agent/codex_responses_adapter.py:1438–1449` supplies the fixture shape.
  Unlike the installed extractor, this helper requires exact phase equality.
- No native modules are imported. Inspected native `agent/redact.py` pure
  prefix/assignment/auth/URL redaction and the interim extractor; use a local,
  bounded stdlib-only subset, independent of configuration, credentials or network.
- Public commentary is assistant prose, NOT a command preview. The command
  `_safe_detail` allowlist incorrectly hid Unicode, Markdown and harmless words
  such as token/password/credential. Public Unicode, punctuation, backticks,
  newlines and whitespace now survive exactly outside removed private/secret spans.
  Existing whitespace/whole-message-suppression assertions changed for that reason;
  actual-secret assertions remain. Already-redacted sentinels are public text.
- Redact values of credential assignments (including quoted JSON), Bearer/Basic
  credentials, recognized provider prefixes (OpenAI, GitHub, HuggingFace, npm,
  PyPI, Groq, Slack, AWS and Google), JWTs and PEM private-key blocks. Generic
  credential words alone never cause suppression. This is pattern redaction,
  not detection of every possible arbitrary unlabeled secret.
- URL references lose userinfo, query and fragment (deliberately stricter than
  native ordinary rendering); URL escapes are decoded at most three times for
  detection. Unaffected public URLs and prose are not normalized. Malformed URL
  authority is replaced with a redaction marker. No native redaction toggles apply.
- Think/thinking/reasoning/reasoning_scratchpad/thought blocks are removed
  case-insensitively, including nested blocks and unterminated inline blocks.
  Parts are concatenated before stripping so split tags cannot leak.
- Sidecar limit: 262144 UTF-8 bytes; maximum 128 raw items, 4096 parsed nodes,
  depth 12. Parsed input receives a bounded traversal too. Entire malformed or
  oversized sidecars are rejected, never truncated into valid-looking JSON.
- Total accepted raw output text budget is 10000 characters per row. Oversized
  items are omitted rather than cut across a private block or credential.
- Projection IDs are `native:{integer-row-id}:commentary:{original-item-index}`;
  provider IDs and private fields are never copied. Nonnegative integer row IDs
  are required. Order and original indexes survive filtering.
- Journal overlay duplicate suppression belongs to catalogue integration and
  must use proven row/turn identity; this helper does not guess from text.

## Verification

Acceptance was written first; test run failed for the absent helper, then
passed. Follow-up adversarial tests failed before safety/bounds implementation.
Regression RED: Unicode/Markdown preservation and whitespace assertions failed
against the command sanitizer before implementation. GREEN: updated focused
projection suite 5 passed; projection plus command-presentation regression suites
36 passed (0.25s). Full-suite run was not repeated within the bounded handoff.
