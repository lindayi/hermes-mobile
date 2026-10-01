# Pending deletion summaries: acceptance contract

`GET /hermes/app-api/sessions` (the existing sessions-list endpoint) adds a top-level
`pending_deletions` array when unresolved operations exist for the authenticated
owner and their currently bound profile. The actual route prefix remains the
application's existing `BASE`; this does not add an endpoint.

Exact entry shape:

```json
{"id":"<root session ID>","status":"unconfirmed"}
```

- Source: durable `session_deletion_operations` rows in `prepared` or
  `native_unknown`, not alias tombstones in `session_deletions`.
- Only the current owner's user ID and current profile qualify. Non-owners get
  no summaries. Root session IDs are distinct; related aliases are never entries.
- At most 100 entries, deterministically ordered by root session ID. Summaries
  are independent of the catalogue's `q`, `kind`, `limit`, and `offset` filters.
  Resolving an entry lets later refreshes expose remaining entries beyond the cap.
- Omit the key when there are no matching unresolved operations, preserving
  existing response shape. Do not alter `items`, `total`, or their pagination.
- Status is the fixed allowlisted string `unconfirmed`; no operation IDs, error
  strings, prompts, outputs, content, titles, or arbitrary state strings appear.
- Listing is journal-read-only and makes no native mutation or receipt lookup.
  Existing capability GET/attestation remains unchanged. Summaries remain visible
  even if deletion capability is currently unavailable, so refresh cannot hide an
  unresolved operation just because the native listener is temporarily unavailable.

## Recovery acceptance

The UI may display “Deletion status unconfirmed” and explicitly request only
`GET /sessions/{id}/deletion` using a listed root ID. Existing authentication,
owner/profile binding, native attestation, and DELETE CSRF/Origin requirements
are unchanged. No automatic DELETE or mutation retry is introduced.

- Exact durable success: existing GET returns `{id,deleted:true}`; next list omits
  the pending entry and keeps the completed tombstone hidden.
- Exact durable refusal: existing GET returns safe 409, releases its claim; next
  list omits the pending entry and restores the unchanged native catalogue row.
- Unknown/unavailable receipt: existing GET returns safe 503; list retains the
  pending entry. A persisted `prepared` claim is also unresolved after restart.

Tests use disposable local databases and mock native transport only; no live
native writes, full-suite run, service restart, or destructive retry is needed.
