# FAMILY native runtime activation (console only)

This tooling activates **normal native conversations and the member's own configured
agent tools**. It does not provision people, credentials, passkeys or integrations.
No production profiles/configuration/services were changed while developing it.

**Chat activation and scheduling/delivery setup are separate operations.**
Use the implemented [family-jobs.md](family-jobs.md) workflow after activation.
This chat activator reports `jobs_ready: false`, never creates a `delivery_tokens` or
`job_delivery_targets` entry, and refuses pre-existing member delivery mappings.
A native API listener does not run cron. Do not claim that family scheduled jobs
will execute or deliver merely because chat activation succeeded. See the final
section for the distinct scheduler deployment requirements.

## Verified native behavior and isolation limits

Official reference: <https://hermes-agent.nousresearch.com/docs/user-guide/profiles/>
and <https://hermes-agent.nousresearch.com/docs/reference/cli-commands>.
Installed source inspected under `/usr/local/lib/hermes-agent`:

* `hermes_cli/main.py::_preparse_profile` processes `hermes -p NAME ...` before
  most imports; `HERMES_HOME` is an import-time boundary for many native modules.
* `hermes_cli/profiles.py::create_profile`: clean creation with `--no-alias
  --no-skills` does not clone owner config, credentials, integrations or skills.
  `.env` exists even when empty; an empty credentials file is not readiness.
* **Normal native profiles intentionally fall back to root OAuth.**
  `hermes_cli/auth.py::_global_auth_file_path`, `_load_global_auth_store`, and the
  shared Nous store make merely setting `HERMES_HOME` insufficient for FAMILY.
* `hermes_constants.get_default_hermes_root()` normally strips
  `/profiles/NAME`. The member listener pins this function **inside its own
  process, before native imports**, to the member home. It clears inherited
  environment credentials, sets `HOME`, `HERMES_HOME`, XDG paths and shared-auth
  directory inside that home, and loads only that member's `.env`. No installed
  native source is patched; owner mode retains its original root/environment.
* `hermes_state.py::_default_db_path` uses the current home's `state.db`.
  `gateway/platforms/api_server.py` has authenticated capabilities, explicit
  session creation, session chat, Runs and request-ID-bound approval endpoints.
* Only reserved `mobile_activation_*` sessions use a special native `AIAgent`
  with **no tools, no fallback model, one iteration, no memory/background review,
  and no context-file injection**.
  Ordinary session creation delegates unchanged to the native adapter, including
  its configured tools, models, context and approvals. A global empty API toolset
  is NOT required. Reserved smoke sessions ignore request-selected models and
  resolve the member's explicitly configured provider instead.

These are logical identity/credential boundaries, **not an OS filesystem sandbox**.
Normal tools still run with the service's Unix UID. Use separate Unix users or a
reviewed container/sandbox before promising hostile-user filesystem isolation.
Do not copy the owner's CLI credentials or personal skills into member homes.

## Prerequisites (operator must actually satisfy these)

1. The member has registered their own passkey using the owner-issued invitation.
   There must already be a real `users` row with role `member`, status `pending`,
   and profile exactly `member_<user-id>`.
2. The existing owner-only provision operation has completed with the durable
   `provisioning:<user-id> = provisioned` marker. Partial/orphaned folders require
   reconciliation; this activator never creates profiles or repairs that marker.
3. The member has independently supplied provider credentials in their own `.env`
   or `auth.json`. Do not clone/mirror root auth. Normal `hermes -p ... model` may
   advertise the owner's fallback OAuth; that is NOT evidence of member auth.
   Independently onboarding OAuth is still an operator/provider login gate.
4. Set an explicit `model.provider` (not `auto`) and `model.default` in that
   member's config, using native config commands. For example, for a genuinely
   supplied OpenAI key and a model the member's account can access:

   ```sh
   hermes -p member_ACTUAL_ID config set model.provider openai
   hermes -p member_ACTUAL_ID config set model.default ACTUAL_ACCESSIBLE_MODEL
   ```

   Enter the member key privately into the member's existing `.env`; do not
   paste it into chat, CLI arguments or shell history. Configure only tools and
   tool credentials that this member is allowed to use. No owner WhatsApp,
   calendar, mail or other integration settings are copied by this procedure.
5. Profile files, listener descriptor, mobile config and auth DB must be owned
   by the service user and private regular files (no symlinks/hardlinks). Tighten
   `.env`, `config.yaml`, `SOUL.md`, and any existing `auth.json`, `state.db`,
   `state.db-wal`, `state.db-shm` to mode `0600`; use `0700` for private directories.
   **Native CLI-created DBs can be `0644`: inspect actual modes, do not assume
   native creation is private.** The activator also requires the default/owner
   native DB to be private, to read only the challenge-absence proof.

Neither displaying a configuration nor a provider resolver returning a key is
sufficient. Activation itself performs a real model call and can incur a small
provider charge. Tests mock that boundary and make no model calls.

## Separate native API listener

Create a dedicated **private JSON file**, separate from the mobile backend's
config, e.g. `/home/lindayi/.local/share/hermes-mobile-live/member_ACTUAL_ID.json`:

```json
{
  "hermes_home": "/home/lindayi/.hermes/profiles/member_ACTUAL_ID",
  "port": 18643,
  "upstream_token": "GENERATE_A_NEW_RANDOM_PRIVATE_TOKEN_AT_LEAST_32_CHARACTERS"
}
```

Use a different unused unprivileged port for every member. `18642` is reserved
for the existing owner listener. The host is always `127.0.0.1`; custom URLs,
host fields, paths and other descriptor keys are rejected. A missing home with
an explicit member port is rejected instead of falling back to owner mode.
The token is generated independently (e.g. Python `secrets.token_urlsafe(48)`),
not copied from either owner or another member. Store it using `O_CREAT|O_EXCL`
and mode `0600`; never print it or put it in an `ExecStart` argument.

Example user-service template, replace `ACTUAL_ID` only after checking the real
member row and profile. This is a **separate service**, not a replacement or
restart of the owner's `hermes-mobile-api` or normal Hermes gateway:

```ini
[Unit]
Description=Hermes FAMILY native API member_ACTUAL_ID
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/lindayi/projects/hermes-mobile
Environment=HERMES_MOBILE_CONFIG=/home/lindayi/.local/share/hermes-mobile-live/member_ACTUAL_ID.json
ExecStart=/usr/local/lib/hermes-agent/venv/bin/python /home/lindayi/projects/hermes-mobile/backend/native_api_service.py
UMask=0077
NoNewPrivileges=true
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Save as `~/.config/systemd/user/hermes-mobile-api-member_ACTUAL_ID.service`.
After explicitly authorizing that member's deployment, the operator can use
`systemctl --user daemon-reload` and
`systemctl --user enable --now hermes-mobile-api-member_ACTUAL_ID.service`.
Do not expose this port in nginx or bind it publicly. Starting the member service
is an operator step, not something the activation CLI does.

Default compatibility: an owner config without `hermes_home` still uses
`/home/lindayi/.hermes`, `127.0.0.1:18642` and the original `upstream_token`.
Member mode never reads that owner config or `.env` to obtain runtime credentials.

## Activate and verify

Run from the repository with its environment (PyYAML is required), **or** with
the installed native interpreter, which already contains the needed libraries:

```sh
cd /home/lindayi/projects/hermes-mobile
.venv/bin/python -m backend.member_runtime \
  --config /home/lindayi/.local/share/hermes-mobile-live/config.json \
  --member-id ACTUAL_ID \
  --runtime-config /home/lindayi/.local/share/hermes-mobile-live/member_ACTUAL_ID.json
```

Alternative interpreter: `/usr/local/lib/hermes-agent/venv/bin/python` with the
same `-m backend.member_runtime ...` arguments. `--help` on both interpreters is
safe and does not load live configuration.

The CLI:

1. Validates pending canonical membership, provision marker, files, explicit
   provider/model, independent endpoint/token and absence of conflicting routes.
2. Authenticates directly to loopback with proxy environment and redirects
   disabled. Requires native capabilities and this sidecar's isolated-credentials
   and reserved-session smoke policy.
3. Creates a random native session/title. Reads that exact row in the member's
   actual DB, and proves that ID is absent from the owner's DB **before** calling
   a model. Hardlinks/symlinks cannot substitute the owner's DB.
4. Requests a harmless random literal response using the reserved no-tools native
   smoke path. Requires that exact answer both in the response and as an actual
   assistant message in the member DB; checks owner absence again.
5. Rechecks pending status under an auth write transaction (revocation wins),
   rejects concurrent mobile-config edits, atomically fsync/replaces the private
   config with `profiles[member]` and `gateway_profiles[member]`, and only then
   commits `users.status = ready` plus the smoke session audit marker.

It never prints API/provider secrets or upstream error bodies. Failure returns a
nonzero exit code. A retained harmless smoke session is deliberate diagnostic
history; it is never deleted from someone else's DB.

**Load the mapping:** after successful activation, an operator must reload/restart
only the mobile backend so its in-memory settings include the new profile route.
A missing route fails closed, but a restart reminder alone is **not** sufficient
if an old backend has a stale member-to-owner mapping cached. Deployment must
include the separately reviewed activation-to-loaded-mapping binding check; never
expose ready-member access based on status alone. No service is restarted
automatically. The status endpoint reports persisted activation plus profile
completeness, not a live health guarantee.

Atomicity/recovery: config replacement is atomic and auth readiness commits last.
A crash between them can leave a verified mapping on disk but the member remains
pending. Re-run the command: it accepts an identical staged mapping and performs
fresh verification. Never hand-set `users.status='ready'`. Conflicting mappings,
revoked members and partial provisioning require manual reconciliation. Coordinate
other config-writing deployment tools; they should honor the stable
`config.json.activation.lock` rather than racing the final compare/replace.

## Scheduling / notifications: separate verified setup

The member API listener deliberately runs no gateway or cron loop. After chat
activation, follow [family-jobs.md](family-jobs.md): prepare independent delivery
credentials, reload the BFF mappings, run the bounded real native script/inbox
verification, then explicitly enable the verified scheduler-only service.

`backend/member_scheduler.py` retains native execution identity and fire claims,
uses member-only credentials, holds an exclusive scheduler lock, and enforces
the exact mobile-only destination at dispatch and delivery. It never starts
another default gateway or a WhatsApp adapter. A reviewed deployable template is
installed, with no member instances enabled until their real setup passes.

Creating a profile or activating chat alone does not certify scheduled delivery.
The scheduler refuses missing, stale or mismatched delivery proof. Physical phone
push permission and lock-screen delivery remain device-only checks.
