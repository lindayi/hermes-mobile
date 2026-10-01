# FAMILY jobs: explicit preparation, verification, scheduler-only service

This is the supported console workflow for an **existing, provisioned and
runtime-activated FAMILY member**. It does not register accounts, provision a
profile, configure OAuth, mark users ready, install/start services, or copy owner
credentials. No real member credentials were supplied during implementation;
only isolated temporary fixtures were exercised. Do not run these commands for
`default`, or substitute an owner's profile for a member.

## Prerequisites / remaining real gates

1. An operator has selected the actual generated profile `member_<member-id>`;
   the auth store records provisioning complete and runtime activation complete.
   Complete [family-runtime.md](family-runtime.md) first. Activation's durable
   proof must match this exact member home, listener URL and listener token.
2. Configure the member's **own** provider credentials and explicit model in that
   profile using the existing native setup procedure. Do not copy the owner's
   `.env`, `auth.json`, credential pools or gateway configuration. Activation must
   already have passed its real model/session smoke test.
3. The mobile backend must load the activated profile/home/gateway mapping.
   Before verifying jobs, it must also reload the newly prepared delivery maps.
   Restart/reload only the mobile BFF through the established operator procedure;
   do not restart the owner's gateway or add a default scheduler.
4. Keep the member scheduler stopped until verification below succeeds. Do not
   run a native member gateway, `hermes cron daemon`, or manual native cron runner
   alongside this service. This service is the member's only scheduler.

Run commands from `/home/lindayi/projects/hermes-mobile`. App-only preparation
uses `.venv/bin/python` (Python 3.12); actual native verification and service use
`/usr/local/lib/hermes-agent/venv/bin/python` and its installed native SDK.
`CONFIG` is the existing private mobile application JSON, not a Hermes YAML file.
Both `MEMBER_ID` and `PROFILE` must be explicitly selected by the operator.

```sh
.venv/bin/python -m backend.member_jobs --help
.venv/bin/python -m backend.member_jobs prepare \
  --config "$CONFIG" --member-id "$MEMBER_ID" --profile "$PROFILE"
```

Preparation creates private `.mobile-jobs/token` and `runtime.json` inside the
member home. The recipient binding is exactly `{"member":"profile"}`. A new,
independent random token is staged, then both app settings are committed in one
atomic config replacement:

- `delivery_tokens[PROFILE]`: that private token
- `job_delivery_targets[PROFILE]`: exactly `mobile_delivery:member`

The shared `CONFIG.activation.lock` serializes this with runtime activation.
Existing owner entries, unknown app settings, member native config, and all jobs
are preserved. A crash before the app config commit can leave an inert private
credential directory; retry reuses it rather than rotating a partially staged
credential. A conflicting existing mapping fails closed and needs manual
reconciliation. **Preparation never starts or enables a service.**

## Real bounded verification (operator only)

After the BFF has reloaded both delivery maps:

```sh
/usr/local/lib/hermes-agent/venv/bin/python -m backend.member_jobs verify \
  --config "$CONFIG" --member-id "$MEMBER_ID" --profile "$PROFILE" --timeout 60
```

This command:

1. Revalidates activated membership, canonical home, descriptor, private token,
   app settings and exclusive member scheduler ownership.
2. Starts a fresh, credential-isolated native worker. Before SDK/auth imports it
   clears inherited environment, pins member HOME/HERMES_HOME/shared auth/XDG
   locations and pins `hermes_constants.get_default_hermes_root` to the member.
   Only that member's `.env` is loaded; isolation anchors are reapplied.
3. Creates **one** harmless future one-shot `no_agent=True` script job that prints
   a random verification challenge. No model call or agent tools are involved.
4. Makes an authenticated **silent** ingress probe using that existing native job
   ID. A stale/unloaded BFF token or ownership/runtime mapping is rejected before
   execution. The probe creates no inbox item. The current bridge endpoint is
   `http://127.0.0.1:9120/hermes/app-api/internal/deliver`, with Origin
   `https://lindayi.me`; it is not an arbitrary remote URL.
5. Uses the actual native `tick(sync=True)` and actual mobile adapter. The native
   due-job selector is scoped to the one verification ID so no existing jobs are
   claimed or advanced. Native tick lock, execution ledger, fire CAS and heartbeat
   remain native. Delivery uses the actual native execution ID, never a made-up
   timestamp or run identifier.
6. Reads the private **real web-inbox SQLite row** for this member, job, run and
   exact challenge. An HTTP success alone is insufficient. On success it writes a
   credential/runtime-bound private `verified.json` with receipt and native IDs.
7. Removes only its own job/script. Inbox proof, native outputs and execution
   audit records remain; existing jobs/scripts are not deleted or run.

`--timeout` accepts 10–120 seconds. A native cancellation timer limits dispatch;
CLI additionally uses a separate process-group watchdog (deadline + 5 seconds)
then a separately bounded 10-second owned-attempt cleanup worker. A failed or
hung worker cannot print success. If cleanup itself cannot validate the member
(e.g. simultaneous revocation) or obtain its lock, the whole command fails closed;
reconcile only the `family_verify_<attempt>.py` job/script, never bulk-delete jobs.
Errors intentionally redact native/provider response bodies and credentials.

Success confirms server-side native execution → adapter → persisted web inbox.
It does **not** prove a physical phone displayed a push notification. The member
must sign in, see the verification inbox item, opt in to notifications on their
actual device, and verify a real push separately. Do not claim the family member
or device is operational until these real-credential/device gates are completed.

## Per-member user-service template — disabled until explicit operator enable

Only after successful runtime and delivery verification, save this template as
`~/.config/systemd/user/hermes-family-scheduler@.service`. Substitute the absolute
private app config path in `ExecStart` yourself. `%i` is the actual **member ID**,
not `default` and not the already-prefixed profile name.

```ini
[Unit]
Description=Hermes FAMILY member %i scheduler (mobile delivery only)
ConditionPathExists=%h/.hermes/profiles/member_%i/.mobile-jobs/verified.json

[Service]
Type=simple
WorkingDirectory=/home/lindayi/projects/hermes-mobile
ExecStart=/usr/local/lib/hermes-agent/venv/bin/python -m backend.member_scheduler --config /ABSOLUTE/PRIVATE/mobile-config.json --member-id %i --profile member_%i
UMask=0077
Restart=no
TimeoutStopSec=20
KillMode=control-group
NoNewPrivileges=true

[Install]
WantedBy=default.target
```

Do not add owner `EnvironmentFile` entries. Do not point the unit at the native
API service or `GatewayRunner`. Installing this template or running
`daemon-reload` alone does not enable it. Only the operator, after the gates above:

```sh
systemctl --user daemon-reload
systemctl --user enable --now "hermes-family-scheduler@$MEMBER_ID.service"
systemctl --user status "hermes-family-scheduler@$MEMBER_ID.service"
# Stop before any re-verification or credential/mapping maintenance:
systemctl --user disable --now "hermes-family-scheduler@$MEMBER_ID.service"
```

No command in preparation/verification executes `systemctl`. The unit's condition
is convenience only: the entrypoint independently checks the bound verification
proof and real persisted receipt, activation, token and mappings. Deleting the
proof receipt deliberately closes readiness and requires re-verification.

## Runtime invariants and limits

- Exactly one outbound mobile adapter. No API listener, WhatsApp adapter,
  GatewayRunner, native gateway ticker or second default scheduler is started.
- `.family-scheduler.lock` is held for the whole process; the SDK additionally
  owns `.tick.lock`, native execution IDs and durable fire claims. The inspected
  SDK `can_dispatch` is a **zero-argument global gate**, not a per-job predicate.
- Every record must explicitly say `deliver=mobile_delivery:member`. Missing,
  local, origin, all, owner, WhatsApp and fan-out destinations fail closed.
  Validation happens before tick **and again at the actual claimed
  `run_one_job` and delivery boundaries**, preventing a changed-job snapshot from
  bypassing the destination check.
- Native inherited/default delivery routing is not used. The process-local
  delivery boundary calls only the mobile adapter with recipient `member` and
  unchanged native job/execution IDs. No standalone/fallback routing can send to
  an owner's channel. No installed SDK/source-system files are patched.
- Corrupt/malformed native job stores and symlinked scheduler paths fail closed;
  there is no automatic unsafe store repair. Resolve bad jobs explicitly before
  starting, including invalid disabled records. Token/runtime changes and member
  revocation stop further admitted executions/deliveries; already-running work
  is not a transactional revocation sandbox.
- This is profile/credential isolation, **not an OS sandbox**. Native model jobs
  can use the member's configured tools under the same Unix account. Tool policy
  and stronger process/filesystem isolation remain an operator responsibility.

## Tests / safe development verification

```sh
.venv/bin/python -m pytest tests/test_member_jobs.py tests/test_member_scheduler.py -q
/usr/local/lib/hermes-agent/venv/bin/python -m backend.member_jobs verify --help
/usr/local/lib/hermes-agent/venv/bin/python -m backend.member_scheduler --help
```

Tests use temporary fake member profiles and transport boundaries. When the
installed native interpreter exists, they really run native tick, subprocess
script execution, the mobile plugin, FastAPI ingress and durable inbox storage,
including stale-loaded-token failure and cleanup. They never operate on real
family/owner credentials, services or production jobs. Native integration tests
skip explicitly on machines without that interpreter.
