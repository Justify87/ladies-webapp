# Issue #56 — Pricing works completely

- **Status:** IMPLEMENTED, NOT VERIFIED
- **Issue:** #56
- **Goal:** Preserve automatic/manual detection, inline signal/artifact, proposal review/edit, accept/reject, current pricing and history.

## Open implementation steps
- [x] Pricing is integrated into new chat state/rendering.
- [x] Automatic message signals expose inline proposal actions.
- [x] Manual chat-level pricing detection is reachable from the V13 chat header.
- [x] Existing pending proposals reopen instead of starting a competing proposal.
- [x] Proposal review/edit and accept/reject flows use the productive API.
- [x] Current/history profile presentation exists.
- [ ] Manual runtime verification of detected/manual → edit → accept/reject flows.

## Acceptance Criteria
- [ ] Pricing remains inline with source messages and all productive actions work in the running Explorer.

## Verification
Source/API contract audit completed. Runtime/UI verification remains manual by design; no browser/E2E CI is required.

## Relevant commits
PR #69 pricing migration and manual-entry completion commits.

## PR / review notes
No active review thread.

## Result
IMPLEMENTED, NOT VERIFIED.
