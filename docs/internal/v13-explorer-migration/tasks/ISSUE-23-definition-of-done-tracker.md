# Issue #23 — Definition of Done tracker

- **Status:** PARTIAL
- **Issue:** #23
- **Goal:** Keep the active V13 migration acceptance items synchronized with code, manual verification, release validation and PR #69.

## Open implementation steps
- [ ] Complete and verify remaining child tasks #26–#36, #38–#59 and #67–#68.
- [x] Keep the lightweight static CI green for the latest code-changing migration commit.
- [x] Harden the release builder so the completed ZIP is checked against the selected release file set and the complete tracked V13 Explorer asset set.
- [ ] Execute the migration-head release build and fresh install/start acceptance for #67/#68.
- [ ] Update tracker checkboxes only when evidence exists.

## Acceptance Criteria
- [ ] Every active DoD item has real verification evidence.
- [ ] Release package content and fresh install/start are verified.

## Verification
Development CI is intentionally limited to static syntax checks. Explorer runtime behavior is verified manually. Release packaging is self-validating at build time, but #67/#68 remain open until an actual migration-head package is built and installed cleanly.

## Retired items
- #63 — Browser smoke tests: retired.
- #64 — Functional E2E tests: retired.
- #65 — Visual reference tests: retired.
- #66 — heavyweight/final CI gate: retired.

## PR / review notes
PR #69 remains open/draft and is the sole V13 migration PR. It must not be auto-merged as part of acceptance.

## Result
PARTIAL — source-level implementation is complete; manual functional verification and release-package acceptance remain the migration-level gate.
