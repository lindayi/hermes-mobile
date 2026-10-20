# Finance dashboard operations and evidence model

The Finance Observatory is a read-only owner companion to Hermes Mobile. Its public shell is `/hermes/finance/`; all financial responses come from `/hermes/app-api/finance/` and require the existing full, ready, default-profile owner passkey session. Opening the ordinary Hermes login and returning to Finance reuses that cookie. Family/member authentication is not permission to read owner financial data.

## Storage boundary

The database is resolved from the explicitly configured default profile root at `finance/finance.sqlite3`. It must be private and outside public/static roots. It is not created, migrated, seeded, copied into the static tree or updated by requests. Missing/incompatible data gives an unavailable response. The frontend contains no financial rows. Browser persistent storage and service-worker caches must not contain responses. Session loss removes rendered financial content. The application shares the existing HTTPS origin and trusts that origin's other code; a folder is not a separate browser security boundary.

The API supports only fixed bounded query shapes with parameters. There is no SQL execution, arbitrary table selection, source-file download, raw document dump or filesystem browser endpoint. Descriptions/evidence are displayed as text; source hyperlinks must be validated as supported public source domains and safe HTTPS URLs. No report is automatically published elsewhere.

## Read models

The column-only contract is `finance-schema-contract.json`. Derived observations/classifications are authoritative only with their source dates and confidence. `hierarchical_transactions` already handles eligible records, allocations and recoveries. Do not mix it with unsplit source transactions in totals. Transfer pairs use two endpoints but one movement count. Partial/missing receiving legs and in-kind/reversal flows are separate.

Optional `dashboard_positions` provides source-backed bank/debt observations not standardized in the investment views. Columns: account primary key, kind, as_of, currency, amount_minor, allocation, planning_protected, source_id, evidence. Kind is cash, liability, mortgage or heloc; liabilities are positive owed, negative credit. It excludes investment totals (already represented elsewhere). The supplied schema assumes one preferred observation per account; refresh it from reviewed evidence through the private ingestion workflow, not a request handler. Without it, bank/debt coverage is explicitly unavailable, never zero.

Optional `dashboard_notes` stores contextual findings in plain text: note_id primary key, topic, title, body, confidence, source_id, as_of. These notes can explain protected funds, refinancing uncertainty, incomplete coverage, tax assumptions and outstanding evidence. They are supplemental context, not additive cash or asset records. Do not expose unrestricted native memories or arbitrary files.

The user confirmation and original-source review pipeline remain separate from this dashboard. Changes to private records become visible on later API requests; chart totals are not generated into public assets. A historical source date does not become current when a page is loaded today.

## Quotes and estimated valuations

Only a fixed allowlisted set of public symbols is requested from external services. No account IDs, holdings quantities, user identity or private transaction information is sent. Current-price estimates depend on last-known share quantities and are not a refreshed broker account. Unsupported funds/GICs stay dated observations, not invented live quotes. Provider failures, stale timestamps and FX observation dates are visible. The bank exchange rate is an indicative reference, not an executable conversion rate. Sources can be delayed/unavailable, particularly unofficial quote endpoints.

Unvested award estimates are gross contingent compensation, not owned liquid assets/current cash. Scheduled vest dates never auto-create vest transactions. Fractional theoretical allocations do not establish actual rounded/net delivery. Grant reference value, quoted current-price estimate and eventual cash sale are different metrics. No trading, tax, payroll or bank execution is implemented.

## Development acceptance

Use synthetic fixtures with the managed repository runner. Tests must verify auth before private DB/network access, readonly state, query/row/network bounds, role isolation, no-store headers, safe text/links, date/currency/scope semantics and exact chart-to-drilldown predicates. Verify nested stable `finance/index.html` with fingerprinted dependencies through the generated-assets builder; public source excludes user data. New source-family hashes are required by the existing fixed integration closure; update literals only after inspecting final candidate bytes, keeping historical pin overlays intact.

Browser acceptance includes narrow/wide layouts, seven views, error/locked/empty states, chart table alternatives, filtering/paging and clearing on401/403. Automated virtual fixtures do not certify physical device passkey gestures. Authorized operational DB reconciliation is separate from public synthetic tests; no production account/session data becomes a test fixture or public log.

## Release

Use the canonical repo's normal issue, exact-head hosted CI, genuine Copilot review and protected-main merge workflow. Routine guarded bridge deployment uses a verified exact-main artifact and the installed host compatibility gate. It leaves native execution, gateway and WhatsApp unchanged. The current chat may itself hold the drain gate: do not restart or kill it. A scheduled transient worker or published shell alone is not live API activation. Preserve status/logs and verify final release SHA, anonymous denial and public asset bytes before claiming deployment.

Rollback uses the existing controller's source/public preimages; no finance DB rollback or migration is needed because dashboard requests never write it. Source facts remain in the private finance pipeline. Treat live startup data errors as a missing/incompatible-source condition, not permission to reset or silently create an empty finance database.
