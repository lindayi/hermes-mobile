# Session telemetry contract

GET `/hermes/app-api/sessions/{sid}/telemetry`, ready authenticated user, own loaded profile + native session validated (401/409/404 otherwise).

```json
{"model":"persisted model or null","provider":"persisted provider or null","metadata":{"source":"native_session","observed_at":null,"freshness":"persisted"},"context":{"used_tokens":null,"limit_tokens":null,"source":null,"observed_at":null,"estimated":false},"usage":{"scope":"session_lifetime","input_tokens":null,"output_tokens":null,"source":"native_session_totals or null","observed_at":null},"run":{"status":"unknown","last_status":null,"id":null,"source":null,"observed_at":null}}
```

Model/provider are **last persisted native session metadata**, NOT guaranteed live current runtime. Metadata timestamps are unknown; session last_activity_at is not a telemetry observation timestamp. Usage is cumulative session lifetime only. Installed native GET session and SQLite do not expose proven per-context occupancy/window; context is unknown, no percentage should be computed. No global config fallback or guessed model windows.

Session list keeps all existing pagination/filter fields and decorates each item with `run` (same object as telemetry) and `run_status` (same string as run.status). Journal source is `web_run_journal`; observed_at is durable run updated_at epoch seconds. Status values: queued, running, waiting_for_approval, stopping, failed, unknown, idle. completed/cancelled map to idle, with original state in last_status. Native-only rows are unknown, never fake idle. Owned/profile-bound newest journal admission wins; historical failure cannot override newer completed work.
