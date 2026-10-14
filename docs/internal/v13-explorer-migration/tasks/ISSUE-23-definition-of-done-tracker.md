# Issue #23 — Definition of Done tracker

- **Status:** PARTIAL
- **Issue:** #23
- **Goal:** Keep the active V13 migration acceptance items synchronized with code, manual verification, release validation and PR #69.

## Open implementation steps
- [ ] Complete and verify remaining child tasks #26–#36, #38–#59 and #67–#68.
- [ ] Keep the lightweight static CI green.
- [ ] Update tracker checkboxes only when evidence exists.

## Acceptance Criteria
- [ ] Every active DoD item has real verification evidence.
- [ ] Release package content and fresh install/start are verified.

## Verification
Development CI is intentionally limited to static syntax checks. Explorer runtime behavior is verified manually; release behavior is verified by the dedicated release workflows.

## Retired items
- #63 — Browser smoke tests: retired.
- #64 — Functional E2E tests: retired.
- #65 — Visual reference tests: retired.
- #66 — heavyweight/final CI gate: retired.

## PR / review notes
PR #69 remains open/draft and is the sole V13 migration PR.

## Result
PARTIAL — tracker remains the migration-level gate for the active functional and release items.
