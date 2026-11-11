# App UX simplification (issue #141)

The owner authorized finishing interrupted app/UX work, not replacing observable
safety contracts with fewer lines. The original work is preserved in checkpoint
2972466 and draft PR #142; subsequent corrections narrow the delivered change.
Governance and deployment changes are separate. No deployment is part of this PR.

## Accepted slice

- Use Sessions for the list/navigation and session-related actions, accessible
  names, empty states, privacy/deletion copy and errors. Keep New chat and the
  existing in-place search/filter/pagination, touch, keyboard and delete behavior.
  Internal route names, storage keys and CSS hooks are unchanged.
- Compress public static responses under `/hermes` when negotiated and above the
  middleware's size threshold. Preserve decoded asset bytes and security headers.
  Do not compress authenticated API responses, even above that threshold.
- Keep approval decision identity, reentry, uncertainty, stale-response fences,
  bounded exact-detail recovery, reader/focus stability and historical chronology.
  These are real user behavior, not redundant defensive implementation.

## Rejected rewrite and retained evidence

The interrupted rewrite removed cross-route decision retention and older-history
recovery, inferred resolution from pending-list omission, and removed substantial
behavioral coverage. Against the preserved rewrite, the restored held-decision
reentry regression showed `Approval required` instead of `Decision pending`.
Both ordinary and aliased omitted-decision restoration tests failed to make the
required exact-detail read. Seven generated-browser history/reader cases also
failed because the required read never occurred.

Restore the established approval implementation and its original behavioral tests
rather than weakening those assertions. The same three reentry cases and seven
real-browser history/reader cases then pass. The checkpoint's small replacement
suite is not the final contract: in particular, absence from a bounded pending list
must not assert resolution. This PR claims no approval-engine size reduction.

## Verification

Focused commands use `scripts/test.py` and an explicitly selected environment
interpreter. The public PR records observed RED/GREEN and final hosted evidence.
The wording regression fails against baseline with `Untitled conversation` rather
than `Untitled session`; the compression regression fails without public-static
middleware because `content-encoding` is absent.

Real-browser list, header, Inbox and approval checks must exercise generated assets.
Full hosted source-ci, same-run integration-tests, issue-link and genuine exact-head
Copilot review with resolved findings are merge gates. Physical iOS, live accounts,
installed/private host compatibility and deployment are not claimed. Source revert
through normal review gates is the rollback; no data migration is introduced.
