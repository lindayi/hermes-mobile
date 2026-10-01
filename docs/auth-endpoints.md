# Auth API integration contract

Base `/hermes/app-api`. Every POST/DELETE requires exact `Origin: https://lindayi.me`; authenticated mutations additionally `X-CSRF-Token` from `/auth/me`. Cookie `hermes_session`, Secure, HttpOnly, SameSite=Strict, path `/hermes`, host-only. All credential objects are JSON-serialized WebAuthn credentials (binary values base64url).

- `GET /auth/me` → `{user:{id,role,profile,status},csrf_token}`; anonymous/expired/recovery-only =401.
- `POST /auth/register/options {code,display_name}` → `{enrollment_id,options}`. `options` is standard `navigator.credentials.create({publicKey:...})` data. Owner bootstrap or invite only. Bootstrap lifetime 15min persisted from initial configuration; restart does not renew it. Invite consumed only with verified credential.
- `POST /auth/register/verify {enrollment_id,credential}` → me shape + session cookie. Owner `default/ready`; members server-generated `member_<id>/pending`. Pending accounts must be blocked by parent agent route gates until clean provisioning.
- `POST /auth/login/options {}` → `{challenge_id,options}` for `navigator.credentials.get` (discoverable resident credentials, required UV).
- `POST /auth/login/verify {challenge_id,credential}` → me + cookie.
- `POST /auth/logout` → `{ok:true}`, revokes current session.
- `POST /auth/verify/options {}` → `{challenge_id,options}`; `POST /auth/verify/finish {challenge_id,credential}` → `{ok:true}`. Challenges bound to authenticated session and account. Freshness five minutes; failures cannot be bypassed by headers.
- `GET /invites` → `{items:[{id,label,created_at,expires_at,used_at,revoked}]}` owner only, never plaintext code. `POST /invites {label,expires_days:7}` → `{code,invite}` once. `DELETE /invites/{id}` → `{ok:true}`. Mutations require step-up; expiry 1–30 days.
- `GET /members` → `{items:[{id,role,profile,status,display_name,created_at}]}` owner only. `POST /members/{id}/disable` → `{ok:true}` owner + step-up; revokes web sessions. No jobs/runs are deleted or provisioned here.
- `GET /devices` → `{items:[{id,label,created_at,last_active_at,current}]}` active own sessions only. `DELETE /devices/{id}` and `DELETE /devices` (all own sessions) → `{ok:true}`, requires step-up. Label not proof of physical device.
- `GET /passkeys` → `{items:[{id,label,created_at}]}` own keys. `POST /passkeys {label}` → `{enrollment_id,options}` (fresh step-up), then `POST /passkeys/verify {enrollment_id,credential}` → `{ok:true}`. `DELETE /passkeys/{id}` → `{ok:true}` (fresh step-up, cannot remove last key).
- `POST /auth/recovery/codes {}` → `{codes:[...]}` once after step-up; issuing a set invalidates prior unused codes.
- **Recovery contract:** `POST /auth/recovery/options {code,display_name}` atomically consumes an offline recovery code, revokes previous sessions, returns `{enrollment_id,options,csrf_token,recovery:true}` and a 10-minute **limited** cookie. This cookie is rejected by normal `require_user`; it can only enroll a replacement via `POST /auth/recovery/verify {enrollment_id,credential}` with returned CSRF token. That endpoint runs real UV-required WebAuthn **registration**, then revokes old passkeys, remaining recovery codes and limited sessions, and issues a full me+cookie response. A recovery code alone never issues a full session or agent access. Interrupted recovery needs another saved code. No user IDs or role/profile requests accepted.

Ceremonies expire after 5 minutes and are one-attempt. Invalid credentials/challenges return generic 400. Authentication 401, Origin/CSRF/ownership/step-up 403, missing owned records 404, conflict (last key) 409, validation 422, auth rate limits 429. Bodies should be bounded by parent middleware as well as auth router.

Integration callback `service.is_session_active(user_id,session_id)` is implemented for push delivery and streaming revalidation; revoked/expired/limited sessions and disabled users return false. No live profile creation or upstream credentials in this module.

Implementation status: all 23 routes above are implemented. Final component run: **41 passed**, plus one unsuppressed Starlette TestClient HTTPX deprecation warning. Executed RED/GREEN and integrated-suite evidence, security boundaries and known gaps are in `docs/tdd-auth.md`. This is not a physical-phone or public-launch certification.

Auth router enforces 64 KiB request bodies before parsing and 20 mutations per ASGI peer + URL path per 60 seconds (durable SQLite limiter). `X-Forwarded-For` is not trusted by auth; proxy trust must be configured narrowly at deployment. Generic device labels are `Browser`. Successful recovery issuance/start/completion is recorded in the private audit table.
