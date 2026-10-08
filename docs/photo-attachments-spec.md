# Private photo attachments

## Scope and baseline

Baseline: `a5dbc52d835d85658567c57330a3b29bfdc6b124`.

This feature adds JPEG, PNG, and WebP photo attachments to new and existing
Sessions. It preserves text-only runs, model selection, clarification controls,
parallel Sessions, and the existing native run admission/idempotency boundary.
It does not add video, arbitrary files, public image URLs, image generation, or
an external storage/conversion provider.

The mobile backend currently accepts only text in `/runs`; the composer has no
file input. The pinned Hermes source is
`NousResearch/hermes-agent@8911e2e0edf750b104edbdc106d63d6cdac88524`.
Its OpenAI-compatible API accepts normalized `image_url`/`input_image` parts,
and its SQLite transcript serializer strips multimodal user image parts. Its
enabled JSON snapshots and provider-error dumps do not provide that protection
by themselves. The mobile bridge must send a validated image as part of the same
`/v1/runs` input and preserve only opaque attachment metadata in history.
Native image copies, public static URLs,
or base64 in app history/journals/events are prohibited.

## Upload and image contract

Each authenticated upload is bound to the current user, profile, and exact
Session. It uses an opaque server-generated attachment ID and a client
idempotency key; retries with the same key and identical bytes return the same
reservation, while changed bytes conflict. At most four unique attachments may
be bound to one run.

The server streams request chunks to private staging and enforces the 10 MiB
input limit before image decoding. Decoding is based on actual image content,
not filename or client MIME. Accept only single-frame JPEG, PNG, and WebP;
reject malformed/truncated images, SVG/active content, excessive pixels, and
unsupported HEIC/HEIF with clear guidance to choose JPEG, PNG, or WebP. Normalize
EXIF orientation, strip metadata including location, never upscale, constrain
the long edge to 2048 pixels, and re-encode a legible image no larger than
2 MiB. Decode work is bounded by a fixed pixel ceiling and frame count, with one
image decode at a time per backend process. Input
filenames are neither retained nor returned.

## Private storage, quotas, and retention

Image bytes and staging files live under the configured private state directory,
outside public roots and immutable releases. Files are created without following
symlinks and with owner-only permissions. Authenticated reads require the exact
user/profile/Session binding, use opaque IDs, and return only stored normalized
images with `nosniff`, safe content type, and `no-store`.

The configurable defaults are 128 MiB per user and 512 MiB globally, counting
normalized files, staging data, and in-flight reservations. A reservation is
made under the storage database's writer lock before accepting bytes and covers
the maximum simultaneous staging plus normalized footprint. Upload admission
also requires at least 1 GiB of free space. Concurrent uploads and cleanup use
the same serialized accounting; no non-expired linked image is evicted to make
space. Disk-full, low-space, quota, decode, and interrupted-body failures return
bounded actionable errors and release or expire their reservation safely.
Metadata has a separate atomic per-user and global row admission limit. Every
row counts, including receiving rows, expired tombstones, and linked history.
The defaults derive from the byte quotas at one row per 4 KiB: 32,768 per user
and 131,072 globally. Configure `attachment_user_metadata_rows` and
`attachment_global_metadata_rows` to positive integer limits; omission derives
them from the configured quotas. Owner, profile, and Session identifiers are
limited to 512 UTF-8 bytes each; upload keys are limited to 128 ASCII characters.
The fixed schema, bounded identifiers, opaque IDs, and fixed-size image metadata
bound each admitted row and its indexes. Admission reserves another 4 KiB of
free-space headroom for metadata. These limits are capacity controls, not an
attachment-count limit per Session. Existing over-limit stores reject new keys
but still allow reads, existing-key recovery, release, and authorized cleanup.
Admission never erases linked history or shortens tombstone retention to make room.

An image reader holds a shared lock on its open file descriptor. Release and
expiry remove the image and clear its charged size only after they acquire the
exclusive lock; otherwise they retain the charge and retry cleanup later. Kernel
locks end when a reader process exits, so the same rule works across workers and
after a crash or restart.
Opening and reading use bounded workers. Cancellation drains the actual worker
before closing its returned descriptor. The response owns that descriptor and
closes it exactly once, including failure or cancellation while sending response
headers before body iteration, body failure, client disconnect, and completion.
Decoder cancellation likewise drains the actual decoder before removing staging,
releasing its reservation or lease, or returning its worker slot. The event loop
remains responsive while worker ownership is retained. The single decoder permit
is separate from the four storage/read permits, so decoding cannot exhaust read
workers. Follow-up abort and lease cleanup are shielded from persistent ASGI
cancellation while waiting for a storage permit.

Unlinked uploads expire after 24 hours. Linked photos expire 30 days after
upload, independently of the abandoned-upload TTL. Runs in queued, active, or
unknown states pin their attachments until terminal completion or positive
reconciliation; pins do not waive quotas or free-space checks. Expiry removes
only the private image bytes and retains bounded metadata so history can render
an explicit expired-photo placeholder without deleting message text. Startup
reconciliation and a periodic bounded cleanup reclaim expired staging files and
durable orphans without traversing unrelated state. The first inventory must
finish before admission. Routine scans resume across known files without
re-closing admission; discovering an unaccounted orphan fails admission closed
until the bounded reconciliation pass completes.
Eligible-row cleanup keeps a transactional cursor ordered by creation time and
opaque ID. Each bounded batch advances past locked readers and live upload leases;
the cursor survives restart and wraps after reaching the end. Skipped files and
reservations remain intact and charged. A full blocked batch therefore cannot
starve later eligible rows. Run pins, reader locks, and retention authorization
remain required on every visit.
Dead receiving rows remain charged until their deterministic staging, temporary,
and possible JPEG/PNG/WebP publication files are removed under an exclusive upload
lease and database writer lock. Unlink failure keeps the reservation. This covers
publication before the database commit, including across worker processes.
Each traversal also freezes a visit budget equal to the current metadata row
count. It wraps when that budget is consumed even if newer eligible rows keep
arriving. Thus older rows that become eligible after the cursor passed them
cannot be starved by continuous admission; the remaining visit budget is durable.

## Run, history, and recovery

The composer supports camera and photo-library selection on mobile and file
selection on desktop, individual preview/removal, optional text, and images-only
sends. Images-only sends use the explicit default request “Please describe the
attached image(s), including any visible text.” Upload status is announced to
assistive technology; unsupported formats and retryable failures are explicit.
The draft and all selected attachment references remain available after a
recoverable upload or run error, including after navigation and reload when the
run outcome is uncertain. Restored photo previews use authenticated API paths,
and unresolved submitted IDs remain locked against removal until a definite
pre-admission rejection. Cancelled file selection does not alter the draft. A
retry reuses upload and run idempotency keys.

Disabling photos after upload rejects a new photo run with HTTP 503 and code
`photos_disabled_before_admission`. This typed 503 proves no admission;
ordinary 503, network errors, and run conflicts keep the attempt frozen.
An unavailable or timed-out photo capability probe rejects before local run
creation and photo binding with HTTP 503 and code
`photos_unavailable_before_admission`. This typed rejection also unlocks the
original draft and photos. Generic 503 responses remain uncertain.
A proven cancellation before dispatch clears only the captured current attempt.
Legacy unresolved text attempts are frozen with explicit empty attachment IDs;
finish or reconcile that original input, model, and key before sending new photos.

The Photos control shares the existing composer control row so text-only chats
retain their short-viewport geometry. Navigation and teardown make best-effort
authenticated DELETE requests for unbound uploads, including uploads that finish
after navigation. Accepted-run photos are not released. Individual asynchronous
removal resolves the selected item by identity, not by its earlier list index.

Run admission atomically binds the exact attachment IDs to the existing durable
run identity. The bridge reads normalized bytes only for that run and sends
them, with its text, in the native multimodal user message. A run retry cannot
bind different files to an existing idempotency key. The selected native
profile/model remains authoritative; an image run fails with an actionable
error if the configured native vision capability is unavailable, rather than
pretending an image was analyzed.

Image database/file reads and base64 encoding run off the orchestration event
loop. Snapshot aliases use indexed connected-identity queries scoped to the
authenticated owner/profile, not account-wide history scans. Attachment IDs retain
their per-run position order and the snapshot's existing read transaction.
Admission anchors capture the positively verified canonical Session boundary;
prior turns match that same canonical identity after alias sends and compaction.

Reopened history uses native message metadata to bind opaque attachment IDs to
the exact user turn. Authenticated thumbnail/full-image reads are subject to
the same ownership and expiry checks. After expiry, the UI shows an explicit
placeholder and keeps the associated text. No filename, image bytes, local
path, or user-controlled URL is placed in logs, events, notifications, or
native history.

The dedicated owner listener loads the versioned `native_run_controls` adapter
from the staged repository release. It admits complete photo requests up to
20,000,000 bytes, with at most four 2 MiB inline JPEG/PNG/WebP parts and matching
opaque IDs. The existing 10,000,000-byte text/history budget remains independent
of the image bytes. The ingress bound measures wire bytes; text/history budgets
measure compact UTF-8 JSON after parsing, without ASCII escaping or formatting
whitespace. Validation occurs before native run creation. A proven HTTP
413 is terminal rejection, not an unknown network outcome; ambiguous dispatch
still observes the original run without replay.
Before local admission the bridge enforces both byte budgets with the native
compact UTF-8 sanitized-text calculation. A budget rejection returns HTTP 413,
leaves uploaded photos pending, and creates neither a local run nor a native POST.
Before local admission the bridge requires the exact versioned `mobile_photos`
capability, including private persistence and size bounds. An old listener or an
unverifiable capability response is a known pre-dispatch failure: no photo POST
has occurred. This handshake prevents bridge-only deployment from using the
unadapted installed listener. The proof is request-local and binds the owner,
profile, client, endpoint, authorization, input/history, selected model, attachment
IDs, and image sizes. Dispatch verifies that binding without a second handshake.
Already admitted retries do not repeat the preflight; text-only runs add no probe.

The adapter copies and sanitizes data at the actual enabled snapshot, request
dump, SQLite batch/`api_content`, trajectory, and API-request hook boundaries.
SQLite compaction/archive insertion and direct message writes use the same
sanitization, preserving native intrinsic message markers and text metadata.
Provider-error display/status buffers and error hooks are sanitized too.
All native photo-run results, including successful model output that echoes
inline image data, are sanitized copies before status/event persistence.
During a mobile photo turn every lifecycle-hook argument is sanitized in a copy,
including `pre_api_request`, `pre_llm_call`, and `post_llm_call`;
the live provider request and message history are not mutated. Only transient
live provider input keeps image bytes. Analyzer fallback does not materialize
an unaccounted temporary copy for a mobile photo turn; text-only/tool-image
fallback keeps its native behavior. Unavailable analysis or an image rejection
produces an actionable failed photo run instead of a text-only retry.
No new native photo files are retained, so native copies add zero bytes to the
attachment store footprint. This does not certify historical copies or other
listeners. Native source dependencies are fingerprinted by the existing guarded
release; cloud and deployed listeners use the same repository adapter with the
same installed baseline, not a cloud-only compatibility patch. Activation remains
an independently reviewed exact-main guarded release, with the pre-photo source
map retained for drain and rollback. Clarification capability is classified only
from complete accepted source maps: the current and pre-photo maps support it;
older non-clarification maps do not, and unknown or mixed maps fail closed. The
historical and rollback maps remain unchanged. No installed source is hot-edited.

## Security and rollout

Both photo routes offload native catalog lookups and recheck Session ownership
after the await. Catalog contention must not block active orchestration. Malformed
PNG decoder `SyntaxError` is an actionable HTTP 415, with upload cleanup and
same-key retry. Disk-full remains HTTP 507; cancellation and memory errors are
not converted to unsupported-image errors.

Writes retain same-origin and CSRF enforcement. The streaming body limit applies
before framework parsing; authenticated retrieval never accepts a host path or
remote URL. IDs are not authorization: all access revalidates the current
owner/profile/Session binding. Staging cleanup is bounded and cannot follow
symlinks or delete outside the attachment root.

Storage and metadata changes are additive. Text-only requests and legacy
history remain readable. The guarded photo-only rollback sets the private
application configuration field `photos_enabled` to `false` through the normal
reviewed release path. This rejects new uploads and new photo-bearing runs, but
keeps text runs, retries of already admitted photo runs, authenticated reads,
metadata, and normal cleanup available. Re-enable photos through the same
guarded path by setting the field to `true`; do not edit a live config or
immutable release. A rollback to pre-photo code does not preserve photo reads or
cleanup and is not a retention-safe rollback. Neither path deletes production
data or activates production as part of development. Production, real accounts,
private photos, and real model calls are never test fixtures.

## Review-thread recovery boundaries

The photo picker advertises only JPEG, PNG, and WebP; unsupported HEIC/HEIF
still receives explicit guidance if supplied outside the picker filter. A native
HTTP 413 is described as a run-request rejection, not as evidence that a
text-only request contained photos.

Upload lease teardown holds the same SQLite writer transaction used by lease
acquisition and same-key recovery across both descriptor close and pathname
removal. A failed abort may retain a receiving row, but cannot expose a second
lease inode to a concurrent retry during teardown.

Before the native memory/skill background-review fork is spawned, the private
photo agent passes a sanitized copy of the message snapshot. The foreground
vision input is unchanged; the plain background agent never receives inline
photo bytes that could enter its own error dump.

A failed capability probe rechecks the exact owned idempotent submission before
returning a definitive pre-admission rejection. If another matching submission
has already admitted the run, its existing result is returned instead. Mismatched
submissions retain the ordinary conflict checks, and an actual pre-admission
failure without a matching run remains a typed rejection.

## Acceptance evidence

The feature is accepted only when these assembled boundaries pass; helper-only
tests are insufficient. The listed review symptoms are grouped by shared failure
boundary so duplicate findings do not create duplicate fixes.

| Boundary | Observable acceptance | Regression seam |
| --- | --- | --- |
| Native transport and persistence | Four normalized images within the advertised limits reach the pinned native handler and model input in the same run; complete request size is bounded before admission; unsupported vision is explicit; snapshots, transcripts, caches, and backups retain no image bytes beyond the attachment lifecycle. | Hosted test against the exact pinned-and-patched native source, with synthetic images and model-boundary capture. |
| Run and history binding | Requested and canonical Session aliases resolve the same owned attachments; native multimodal placeholder turns and completed latest turns retain ordered opaque IDs on the exact user turn without duplicate synthetic turns. | Route-to-native run, then native-persisted history fixture using the pinned serializer and anchored journal identity. |
| Reservation and storage recovery | Lost responses retry the immutable attachment IDs; dead receiving reservations recover without disturbing live uploads; failed publication leaves no unaccounted file; linked tombstones survive with their run; bounded orphan scans eventually account for every byte before admitting more uploads; periodic known-file sweeps do not interrupt admission. | Restart/race, injected publication failure, pagination through more than one cleanup batch, and quota accounting over private storage. |
| Capacity, expiry, and I/O | Queued, active, and unknown runs pin images consistently through run-time reads; active download bytes remain charged until the reader closes, including across workers and restart; terminal expiry preserves text and bounded tombstones; ENOSPC/EDQUOT produce actionable responses and release reservations; history metadata queries are batched off the event loop. | Synthetic run-state expiry cases, delayed-open cancellation, locked-reader release/cleanup, injected filesystem failures, query-count assertions, and event-loop heartbeat under writer contention. |
| Composer lifecycle and preview | Retry uses the original submitted IDs, input, and model; navigation and reload cannot delete an uncertain submission; added photos remain selectable without changing the frozen retry; definite rejection unlocks photos; steering cannot silently discard photos; selected and restored previews use authenticated URLs and decode under deployed CSP. | Real browser file-input flows for retry, pending selection/removal/navigation/reload, unsupported mixed batches, steering, and both ASGI and Apache CSP. |

Issue #85 adds `backend/attachments.py` and the shared-budget
`backend/native_run_controls.py` dependency to the fixed execution-source
inventory and Python closure. Only the following current candidate bindings supersede
earlier pins; historical fixtures and all review/authorization gates remain
unchanged. These digests establish source consistency, not review, CI, or
activation success.

| Candidate path | SHA-256 |
| --- | --- |
| `backend/native_run_controls.py` | `5383f0cb8be70a795bb2690bc5968c373d6c3f6f0faf4458539983c30476b787` |
| `backend/model_controls.py` | `8e73ee01d61b44786cdaf5798edadfd97eefd12175e942be45e4bb21473849ad` |
| `deploy/native_controls_release.py` | `41b620ce44325feff43339fd05a1fbb2a0036e06467f196948b9a317b876d5dc` |
| `backend/app.py` | `61ece82105971fad63e971ceb56a836f67637707182c5b2f1e8c1c996de2847d` |
| `backend/attachments.py` | `ed8db6bfb377f310969a101ba3be3b5cad03f23c15590680cc6af1af4701f1b4` |
| `backend/hermes_client.py` | `d669f59f7bf3fb2cc5cc671081937fb7328f9b53d50995f966333ae413823116` |
| `backend/task_reminder_presentation.py` | `fa68480f43a29d1543e55b1e514263d8fdc7f25593264ca5a61ac021b021e1c6` |
| `backend/native_catalog.py` | `986c43c3b13885605053330adc52a7f7b25ac9608e81bd1e67839fd3f3cb9e49` |
| `backend/orchestration.py` | `f0bb271c685c6ef7070e3079cbf25a62e3e38c9d3f3512fb04cb8e56fa898598` |
| `backend/runs.py` | `3f4ae4fa3ec533f358b8c0c9de012dbf369c1899f69cee9a28045fe1eadb7113` |
| `requirements.lock` | `ae9402d803d936191d63d62c8d0f577df1303777d7fd9f03eca6191f41804e04` |

Focused regressions must demonstrate RED before implementation and GREEN after
implementation. Synthetic tests cover actual decoded fixtures, malformed and
unsupported content, quotas under concurrent upload, low disk space, interrupted
uploads, cleanup and pin races, exact ownership, idempotent upload/run retries,
the assembled route-to-native payload, and bounded native history. Browser
tests exercise real file inputs, cancel/remove/retry, image-only and
text-plus-multiple-photo messages, accessibility status, reopened and expired
attachments, and unsupported HEIC/HEIF guidance. Physical iPhone/iPad Safari
camera/library behavior and a real configured-model vision response remain
manual post-deployment checks and must not be claimed from mock tests.
