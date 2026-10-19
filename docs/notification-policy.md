# Actionable push notifications — approved design

Approved by owner in WhatsApp: push only useful request outcomes, actionable approvals/blockers, meaningful scheduled results and serious operational incidents; informative previews; independently enable/disable allowed categories. Implement bridge/UI only; preserve native SDK, cron jobs and WhatsApp destinations.

## Settings and shared API contract

All push preferences are per authenticated device (auth session), owned by the current user. Inbox persistence is independent of push preferences. Existing subscribed devices default to enabled categories and informative previews. No automatic permission prompt/subscription creation. A revoked device can neither modify preferences nor suppress other devices' pushes.

GET /push/preferences returns {revision:0,enabled:true, categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false}. PUT /push/preferences accepts that full exact shape; strict booleans/revision integer, unknown fields rejected; auth+CSRF required. PUT revision is an optimistic precondition; successful writes increment it, stale full-document writes return409 and UI reloads/explains rather than overwriting privacy/master changes from another tab. Master/category changes suppress queued unclaimed work, never replay old suppressed events on re-enable. Hide-details applied at actual send time. Settings show one visually distinct device master control above individual options, based on actual permission/subscription/preference state. Options appear only while enabled; category/privacy edits remain local until explicit Save, preserving CAS conflicts and truthful failure/retry. Master toggling does not silently save pending individual edits. Controls: Push notifications on this device; Request completions; Approvals; Action needed or failures; Scheduled updates; Serious server alerts; Hide notification details. Explain per-device scope and unchanged Inbox/WhatsApp. Failed saves visibly revert or remain unsaved; no silent optimistic success.

POST /push/presence accepts {client_id:<random per-tab id max80>,session_id:<owned native session id or null>,visible:<strict bool>,sequence:<nonnegative integer>}. Session ownership verified via injected validator. One row per user/device/client, monotonic sequence fences out-of-order hide/show updates. Heartbeat at 15s while visible focused conversation; expires at 45s. Hidden/expired lease retains bounded monotonic high-water/tombstone, equal/older sequence cannot refresh lease; cap distinct clients and safeinteger sequences. Presence enforces auth/CSRF without extending auth inactivity; avoid nested/inverse cross-DB writer locks. Hidden/blur/navigation/logout removes live presence (best effort; TTL fail-safe); no heartbeat while hidden. Other tabs/devices remain independent. Only exact relevant conversation presence suppresses completion/approval/attention pushes across the same user's devices; generic app/settings/inbox visibility doesn't. Presence uses authenticated requests+CSRF and must never expose content. Check active device validity and relevant session ownership at use time; stale account callbacks cannot refresh presence.

## Producer and delivery policy

NotificationService.ingest gains explicit category keyword ('completion','approval','attention','scheduled','operational','background','test','internal'); optional profile; default unclassified/internal = Inbox only (never implicitly push arbitrary text). Store category in additive side table, not new exposed inbox columns. `store_background` is conversation-only durable Activity/history storage, excluded from attention Inbox and always silent for push, while preserving receipts/ACK exactly. Explicit `category='background'` ingestion is likewise excluded from attention Inbox and push. Existing pending legacy background/release/unclassified work is suppressed, not replayed. Approvals keep exact pending validator and expiry; category=approval, safe actionable text. Cron ingress explicitly category=scheduled; blank/[SILENT]/historical remain silent. Explicit test goes to requesting device only, respects master switch/privacy, independent of category toggles. Unknown categories rejected/fail closed.

Expose service methods get_preferences(user_id,device_id), set_preferences(user_id,device_id,payload), record_presence(user_id,device_id,payload). Inject presence_validator(user_id,session_id) -> bool. Methods and routes enforce limits; preference mutation atomic with suppression and captured bearer generation revalidated at the actual write. Device authorization is fenced through outbox claim commit; revocation before claim must block transport, while post-claim sends cannot be recalled. Provider I/O never holds SQLite locks; cross-store lock order is coherent. flush rechecks policy, read/dismissal, presence, expiry, revocation before claim/send. Inject event_validator(user_id,profile,session_id,category) to recheck event owner/profile binding and current conversation eligibility/tombstones; scheduled/operational nullsession events explicitly allowed only for a valid owner. Dedup remains per event+endpoint. Claimed/sent/provider-queued/displayed pushes cannot be recalled; state this plainly in Settings. Uncertain crash sends never blindly replay. Additive schema alone is not policy-safe rollback: new sender work must use policy-versioned statuses invisible to baseline sender, and durable DB guards suppress legacy pending/retry writes, so exact old-code rollback preserves Inbox/subscriptions but cannot send opt-out-bypassing push. Re-upgrade cannot replay those legacy rows. Read notifications should suppress pending push.

New RequestNotificationService(notifications,journal,*,resolve_user,session_validator,clock=time.time) in backend/request_notifications.py: tick() local-only, bounded, no native network or SDK. resolve_user(id) returns live ready profile-bound user or None; session_validator(user,session_id) validates current eligible session/tombstones. On construction durably establish activation time in notifications DB; no old completed-history flood. Tick scans owned journal runs updated since activation, emits one stable request:<run_id>:terminal event per authoritative completed/failed request. Use only persisted public final `output` (never deltas, tool output, input, private history); failed output must use safe generic reason if raw internal error. Completed without public output uses truthful generic finished text. cancelled/user stop intent stays silent even if races to completed/failed. Unknown >=120s may emit one actionable unresolved-outcome attention event, not pretend failure or resubmit. Later known completion may notify resolution. Queued/retrying unknown alerts must be suppressed once the exact request is known or stopped; a final-send request validator rechecks the trusted run identity, status and stop intent. Positive compression/native-run alias resolution is shared by event eligibility and presence matching; plain parent/text similarity never implies equivalence. Dedupe/restart/reconcile do not lose completed events. No broad punctuation/LLM guessing of clarification or private errors. Explicit unsupported clarification events are not invented; a final follow-up question remains a response completion. Recovered-before-threshold unknown creates no alert. Only bridge-managed requests currently have authoritative run lifecycle; WhatsApp's normal message delivery remains untouched.

OperationalNotificationService in same worker-owned module or separate backend/operational_notifications.py: bounded local-only monitor in bridge lifespan, owner only. Critical disk <1GiB sustained >=120s; recover >2GiB sustained >=120s. Delivery failure sustained >=300s (pending/retry due transport/provider or push_worker_error), with one event per persisted incident plus one recovery push only if its incident was actually sent. Empty/unavailable flushes, expiry, pruning, suppression, and own-alert-only work are unobserved, not proof of delivery recovery; use positive owned transport-success evidence, keep incident identity stable through uncertainty, and retire unclaimed stale incident pushes when recovery is positively confirmed. Native/backend reachability use existing background availability observations if trustworthy (no additional polling/network or broad wiring changes); if unavailable source cannot safely distinguish optional capability/idle, do not invent service-down alarms. Existing background-delivery exceptions sustained >=300s may emit precisely labelled background-delivery incident/recovery, never turn optional capability absence into an outage. Monitor fresh bounded deployment status records for failed/rolled_back/rollback_failed, with stable deduplication and generic safe reason, suppressing preactivation history; routine success is silent and recovery follows an actual prior incident only. App passes an explicit state-derived sibling deployment-status path (synthetic tests stay under temporary roots); no global production-path fallback. Monitor cannot notify total host/bridge outage while dead; document external-watchdog limitation. Do not create new cron jobs/restart native. Don't recursively generate delivery-failure alarms on the monitor's own operational alert; no per-tick repeats. Inbox retains incident when push unavailable, so actual push delivery is best effort.

## Attention Inbox correction

The attention contract in [inbox-attention.md](inbox-attention.md) supersedes the
older background “Inbox only” presentation policy. The `inbox` table remains
internal durable storage, not a promise of attention eligibility. A receipt in
`background_receipts` linked by row ID **and matching owner**, or an explicit
`notification_policy.category='background'`, excludes the row. Never classify by
its title, body, keywords or exception text. Same-owner receipt provenance wins
even when another stored category is present; foreign receipts cannot hide or
retarget an unrelated owner's notification.

Listing applies the shared SQL visibility predicate before LIMIT/OFFSET. Read
counts and clear-read use that same predicate, so hidden read results neither
inflate the cleanup count nor acquire dismissal records from attention cleanup.
Existing read/unread/dismissed background rows follow this rule without a data
migration, blanket mark-read, deletion or ACK mutation. Authorized conversation
Activity/history, compression routing, deletion fences, durable receipt IDs,
retries and dedup remain unchanged. Direct owned-row read/dismiss APIs retain
their existing semantics; they are not needed to remove background Inbox noise.
The schema-bypassing silent release observer retains legacy compatibility: only
an absent policy table omits the category clause; receipt filtering remains and
SQL errors propagate. No schema is created by attention queries.

Main-request outcomes and session-bound execution approvals keep their existing
eligibility, ownership, push preferences, presence suppression, deduplication,
receipts and retry history, but are not attention-Inbox entries. The shared
attention predicate excludes session-bound request notifications identified by
their stored request event and session-bound approval rows identified by
`approval_notifications`. Listing, pagination, read counts, mark-all-read and
clear-read use the same predicate. These rows are not deleted or marked read by
the projection. Scheduled updates, operational incidents and genuinely non-chat
approvals remain visible in Inbox.

Web Push carries the opaque stored notification ID, not a trusted destination.
The service worker builds a same-origin `?notification=ID` URL from that ID and
never uses a payload-supplied URL. After authentication, the API resolves an
owned notification to its current chat and run/request identity, and verifies
the run's session lineage before returning its source-session binding. Legacy
delivered `?inbox=ID` links use the same lookup; non-chat notifications retain
Inbox. Request targets load their exact authorized run and older history as needed.
Approval targets focus the exact chat card and never decide an action. Opening a
chat also reconciles pending session approvals, so push is not required to find
them. The existing step-up, CSRF, owner/run/request binding, expiry and
single-use decision endpoint remain the only decision path. Approval actions are
rendered inline with their command/target and explicit controls, not copied into
Inbox. If no chat is bound, retain the prior Inbox approval destination.
Session-bound approvals are classified only after validating the owned run,
approval, request and native session. Positively classified chat sessions stay out
of Inbox; positively classified cron/test sessions retain their Inbox card and
explicit decisions. Missing or ambiguous bindings remain unavailable, not an
Inbox fallback based only on category or session ID.

The pending-approval list and detail require positive current authority before
returning action details or decision capabilities. Omit individually unverified
list items and reject unverified details, including changed role/profile/status;
an Inbox visibility flag is not an authorization denial. Keep positively proven
non-chat approvals, never infer that exception from failed proof.

Client exact-detail reads are deduplicated only within their run-authority
generation. An unknown-run transition aborts and fences only that run's older
reads; late or aborted results cannot restore controls. Same-owner chat re-entry
may restore a retained dispatched or uncertain decision as a bounded,
non-actionable card matched by the exact owner, canonical session, original run
session, run, request and approval identity. This restoration is independent of
the latest run snapshot: a newer run does not retire, rebind or authorize the
older decision. Revalidate its status through authenticated exact detail,
independent of pending-list enumeration; only fresh exact pending authority may
restore controls after a definitive rejection. A delayed decision 401 may expire
only the same account, authentication lifetime and route that dispatched it.
Fresh exact unavailability may settle a retained definitive 403/404/409 rejection
only when no decision is dispatched or uncertain and the read began at or after
that rejection's authority revision. Keep its exact identity and original turn
chronology on later refresh or re-entry, without restoring pending controls.
Unavailability is not a final native execution outcome and cannot settle an
in-flight or uncertain decision.
Retained rejections show non-actionable revalidation, never Decision pending merely
because fresh authority is required. The unknown-run `details_unavailable` projection
stays recoverable through bounded exact-detail refreshes, including after failed
reads or repeated chat re-entry. An authenticated detail 404 has no body identity:
bind it to the dispatched read and current retained identity, including the read's
authority revision, run generation, owner and route. It may show unavailable only
for that definitive rejection, not a dispatched or uncertain decision. Generic
network failures do not retire the card. Only fresh exact pending authority restores
decision controls; revalidation never retries a decision or passkey ceremony.
The notification target's initial GET, list-triggered refresh and exact recheck
use the same decision-authority rule, including handled and replacement paths.
Use the actual shared read's origin revision, not its consumer's start time.
A pre-rejection initial 404 cannot retire the card or block later fresh pending
recovery. A stale initial unavailable response cannot clear a dispatched decision
or discard its successful POST acknowledgement.

An omitted notification-target approval whose initial exact-detail read times
out or is invalidated by an unknown-run transition keeps a truthful Retry status
action while its exact route and owner remain current. Retry performs only the
authenticated exact-detail read. Aborted, timed-out and superseded responses
cannot update the target; route or account teardown stays inert. Transient
failures remain retryable and never become an authoritative unavailable state.

The list uses request-scoped journal proof batches and one native classification
per source session, then revalidates current authorization. Both list and detail
synchronous database work run in the request threadpool, never on the async event
loop; no authorization or classification cache survives the request.

Push preferences remain independent of Inbox visibility and retention. Visibility
changes do not alter outbox status, receipts, acknowledgements, or retry history.
Regression gates cover HTTP delivery/ACK plus Activity, API list/count/cleanup,
legacy reopen and newest-page saturation, category-only ingestion, title
lookalikes, foreign provenance, legacy schema and failed-query safety. Prior
background Inbox tests now assert durable rows and authorized Activity instead,
retaining routing, crash, compression and owner-isolation checks.

## Preview safety

Server builds bounded plain-text title <=100 chars and body <=180 from already-public final/scheduled content, not raw tools/reasoning. Conservative redaction removes credential assignments, bearer tokens, private-key blocks, secret-bearing URL queries and authentication/recovery codes. Fail closed generic preview if suspicious; no claim to perfectly detect arbitrary secrets. Inspect complete normalized title/body for sensitive material before clipping or removing references. Ordinary HTTP(S)/www source references are omitted so an otherwise safe public outcome or price stays informative; reference-only content, secret-bearing URLs and unsafe non-HTTP schemes use generic fallback. Ordinary colon-and-whitespace labels and balanced Markdown headings remain informative; this does not exempt nonspaced opaque URIs or spaced known-dangerous schemes. No URLs/HTML/Markdown control rendering or remote images in notification. Hide details replaces BOTH title and body with generic text. SW only displays validated bounded title/body from server payload, safe same-origin inbox deep link/tag; legacy/malformed payload generic. Click navigates only; never approves/sends/marks read. No authenticated cache additions.

## Acceptance / regression gates

Observed RED then GREEN for each slice. Synthetic explicit private state and isolated HOME/HERMES_HOME before SDK imports; never run tests against live files. Per-user/device prefs+CSRF+bad types+revocation; all category/master toggles; no replay on re-enable; privacy change during retry; historical migration; duplicate ingestion; exact unread/read/presence suppression; two-tab sequence ordering and lease expiry; foreign/hidden/deleted session rejection; SSE completion and reconciliation persistent journal lifecycle; no child noise; stop race; restart/no backlog flood; operational threshold/dedup/recovery; stale settings/visibility callbacks; generated SW payload/privacy/click behavior at mobile sizes. Keep old lifecycle/safety assertions; adapt only changed intentional policy expectations and document. Full frozen suite and independent exact-byte review before guarded bridge deployment. Preserve live subscriptions, history, current+rollback releases and native/WhatsApp PIDs. Physical iOS lock-screen delivery remains a device check.
