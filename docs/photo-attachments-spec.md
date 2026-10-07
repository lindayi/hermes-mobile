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

Unlinked uploads expire after 24 hours. Linked photos expire 30 days after
upload, independently of the abandoned-upload TTL. Runs in queued, active, or
unknown states pin their attachments until terminal completion or positive
reconciliation; pins do not waive quotas or free-space checks. Expiry removes
only the private image bytes and retains bounded metadata so history can render
an explicit expired-photo placeholder without deleting message text. Startup
reconciliation and a periodic bounded cleanup reclaim expired staging files and
durable orphans without traversing unrelated state.

## Run, history, and recovery

The composer supports camera and photo-library selection on mobile and file
selection on desktop, individual preview/removal, optional text, and images-only
sends. Images-only sends use the explicit default request “Please describe the
attached image(s), including any visible text.” Upload status is announced to
assistive technology; unsupported formats and retryable failures are explicit.
The draft and all selected attachment references remain available after a
recoverable upload or run error. Cancelled file selection does not alter the
draft. A retry reuses upload and run idempotency keys.

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
loop. Snapshot attachment IDs are fetched in one owned-session query, retaining
their per-run position order and the snapshot's existing read transaction.

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
of the image bytes. Validation occurs before native run creation. A proven HTTP
413 is terminal rejection, not an unknown network outcome; ambiguous dispatch
still observes the original run without replay.
Before photo dispatch the bridge requires the exact versioned `mobile_photos`
capability, including private persistence and size bounds. An old listener or an
unverifiable capability response is a known pre-dispatch failure: no photo POST
has occurred. This handshake prevents bridge-only deployment from using the
unadapted installed listener.

The adapter copies and sanitizes data at the actual enabled snapshot, request
dump, SQLite batch/`api_content`, trajectory, and API-request hook boundaries.
SQLite compaction/archive insertion and direct message writes use the same
sanitization, preserving native intrinsic message markers and text metadata.
Provider-error display/status buffers and error hooks are sanitized too.
Only transient live provider input keeps image bytes. Analyzer fallback does not
materialize an unaccounted temporary copy; unavailable analysis or an image
rejection produces an actionable failed photo run instead of a text-only retry.
No new native photo files are retained, so native copies add zero bytes to the
attachment store footprint. This does not certify historical copies or other
listeners. Native source dependencies are fingerprinted by the existing guarded
release; cloud and deployed listeners use the same repository adapter with the
same installed baseline, not a cloud-only compatibility patch. Activation remains
an independently reviewed exact-main guarded release, with the pre-photo source
map retained for drain and rollback. No installed source is hot-edited.

## Security and rollout

Writes retain same-origin and CSRF enforcement. The streaming body limit applies
before framework parsing; authenticated retrieval never accepts a host path or
remote URL. IDs are not authorization: all access revalidates the current
owner/profile/Session binding. Staging cleanup is bounded and cannot follow
symlinks or delete outside the attachment root.

Storage and metadata changes are additive. Text-only requests and legacy
history remain readable. Rollback disables new photo uploads while preserving
the existing text-only API and files until normal retention cleanup; rollout
does not restart the native service or delete production data. Production,
real accounts, private photos, and real model calls are never test fixtures.

## Acceptance evidence

The feature is accepted only when these assembled boundaries pass; helper-only
tests are insufficient. The listed review symptoms are grouped by shared failure
boundary so duplicate findings do not create duplicate fixes.

| Boundary | Observable acceptance | Regression seam |
| --- | --- | --- |
| Native transport and persistence | Four normalized images within the advertised limits reach the pinned native handler and model input in the same run; complete request size is bounded before admission; unsupported vision is explicit; snapshots, transcripts, caches, and backups retain no image bytes beyond the attachment lifecycle. | Hosted test against the exact pinned-and-patched native source, with synthetic images and model-boundary capture. |
| Run and history binding | Requested and canonical Session aliases resolve the same owned attachments; native multimodal placeholder turns and completed latest turns retain ordered opaque IDs on the exact user turn without duplicate synthetic turns. | Route-to-native run, then native-persisted history fixture using the pinned serializer and anchored journal identity. |
| Reservation and storage recovery | Lost responses retry the immutable attachment IDs; dead receiving reservations recover without disturbing live uploads; failed publication leaves no unaccounted file; linked tombstones survive with their run; bounded orphan scans eventually account for every byte before admitting more uploads. | Restart/race, injected publication failure, pagination through more than one cleanup batch, and quota accounting over private storage. |
| Capacity, expiry, and I/O | Queued, active, and unknown runs pin images consistently through run-time reads; terminal expiry preserves text and bounded tombstones; ENOSPC/EDQUOT produce actionable responses and release reservations; history metadata queries are batched off the event loop. | Synthetic run-state expiry cases, injected filesystem failures, query-count assertions, and event-loop concurrency check. |
| Composer lifecycle and preview | Retry uses the original submitted IDs; navigation cannot delete an in-flight submission; photos selected while a run is pending remain available; removal restores composer state; rejected batches revoke every preview; steering cannot silently discard photos; selected previews decode under deployed CSP. | Real browser file-input flows for retry, pending selection/removal/navigation, unsupported mixed batches, steering, and both ASGI and Apache CSP. |

Issue #85 adds `backend/attachments.py` to the fixed execution-source inventory
and Python closure. Only the following current candidate bindings supersede
earlier pins; historical fixtures and all review/authorization gates remain
unchanged. These digests establish source consistency, not review, CI, or
activation success.

| Candidate path | SHA-256 |
| --- | --- |
| `backend/native_run_controls.py` | `d3e3894fab4ea1809c6c085f883e9d04afae5fd9a031684ee7b8abaf09d5b8e3` |
| `backend/model_controls.py` | `d6e351bc68e09aba15a128c61d1d50d09ba5945db7a6438ddac51ddf61c41341` |
| `deploy/native_controls_release.py` | `108b000cbeb1776e0db3edae2f523526ad1e28790265f6ebf402406f11e810d5` |
| `backend/app.py` | `11dde94d9e1574ebab579ea1b45c43d4270deffc8175aac2257f5652a14c8cf6` |
| `backend/attachments.py` | `93181c8474a25a29977b82809e865fa6285ef47354d0fe4ebbb6e218011b4b7b` |
| `backend/hermes_client.py` | `b1d5cb9a6485ed6b53caca597e27cd0a34d1e82af5545e2738d271b48b10d623` |
| `backend/native_catalog.py` | `d6a758a7007dd23f8921bacccdf69adf81ee7b6fa212f9766493eeba43769653` |
| `backend/orchestration.py` | `4a62bc9ffd9eac78b2ad85bb09247f9c8b2519f4226b296537820683c169b3e1` |
| `backend/runs.py` | `79b15df9918cb85599cb3a7db5c978d2abb681afe9c355acdfa6e07f5472884f` |
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
