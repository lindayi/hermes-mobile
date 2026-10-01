# Final answer visible without reopening

User symptom: last tools succeed but final assistant answer only appears after leaving and reopening chat.

Investigation: recent completed production runs contain both nonempty output and persisted delta/done events (read-only shape/length inspection, no private text logged). Live DOM puts output before tool activity; old deployed UI has uncollapsed tool rows. A long tool list therefore remains below the answer and bottom-follow hides the answer above the viewport. Source candidate still uses that reversed order. Separately, SSE route decides EOF from run status after yielding a previously read batch, which may miss a newly committed terminal tail; reproduce independently.

Acceptance cases before edits:

1. Live answer follows tool activity, matching saved history chronology, without reopening. With many successful tools and activity expanded, bottom-follow keeps final answer visible above the composer in actual 390px Chromium.
2. Terminal output replaces partial streamed text, never duplicates it. Tool completion alone does not finalize the turn. Existing reading-position restraint remains unchanged.
3. Stream completion racing a tool batch must deliver remaining deltas and done exactly once before EOF, including replay cursors and multi-page tails. Terminal state and its event must be consistently observable.
4. Existing auth, event deduplication, stopped/unknown handling, privacy filtering and no-resubmission behavior remain intact.

Verification: observe failing tests before minimal changes; run focused and full browser/Python suites. Publication remains separately gated by root-owned public files until owner enables scoped self-deployment. Do not restart the active web bridge inline or claim undeployed code is live.
