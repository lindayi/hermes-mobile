# Chat activity presentation revision

Requested behavior: saved tools should match live foldable cards; WhatsApp-like concise context; foldable reasoning; remove user-message copy buttons; remove decorative filler.

Implementation boundary: private model chain-of-thought is not exposed. Public assistant commentary can be folded into an explicitly labelled Activity summary. Tool cards are an action log, not a claim to reproduce reasoning.

Acceptance cases (write/run failing tests before each behavior change):

1. A contiguous sequence of saved tool calls/results renders one collapsed activity card with status symbol, concise heading, and meaningful rows when expanded. No generic raw logs, JSON blobs, fake unavailable-details disclosure, or empty assistant bubbles. A single tool still gets the same card shape as live.
2. Live tools use that identical component. Start→completion updates a row, retains its short action summary, and preserves open/closed state. Repeated same-named calls remain distinct; reconnect IDs deduplicate; interrupted tools are unknown, never falsely successful. Card collapsed summary communicates latest activity/status. Tool row detail is bounded, no secret-bearing raw arguments.
3. Older-page prepending and latest-on-open behavior retain previous tests. Call/result ID deduplication works across page boundaries. Normal assistant text stays in order between groups.
4. Public assistant commentary renders as a collapsed Activity summary with actual text; private analysis/reasoning fields remain excluded. No fabricated thinking stream.
5. User messages have no Copy button, while assistant answers retain a small accessible copy control.
6. Remove marketing headings/taglines, personal-space label, composer filler and generic caution footer. Keep actual content, navigation, authentication/recovery/security instructions, consent/approval descriptions, errors, and useful empty-state labels.
7. Real Chromium narrow-screen test checks shared live/history card styling, detail expansion, no overflow, scroll anchoring and composer visibility. Final suite and public asset hashes verified separately; root publish needs operator command and an idle bridge.
