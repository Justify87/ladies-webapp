# Issue #55 — Contact assignment works

- **Status:** IMPLEMENTED, NOT VERIFIED
- **Issue:** #55
- **Goal:** Preserve unassigned endpoint candidates, assignment and new-person creation.

## Open implementation steps
- [x] Candidate/probe/assign/create-person effects implemented.
- [x] Unassigned chat uses the productive selected endpoint when opening assignment.
- [x] The active thread is refreshed after assignment/new-person creation so profile and sending state update immediately.
- [ ] Manual runtime verification with a real unassigned WhatsApp contact.

## Acceptance Criteria
- [ ] Unassigned contacts can be linked or converted to a new person correctly in the running Explorer.

## Verification
Source/API contract audit completed. Runtime/UI verification remains manual by design; no browser/E2E CI is required.

## Relevant commits
PR #69 Messaging migration and contact-assignment hardening commits.

## PR / review notes
No active review thread.

## Result
IMPLEMENTED, NOT VERIFIED.
