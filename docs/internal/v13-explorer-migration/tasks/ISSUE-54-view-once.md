# Issue #54 — View Once works

- **Status:** IMPLEMENTED, NOT VERIFIED
- **Issue:** #54
- **Goal:** Preserve one-time media without render-time prefetch/consumption.

## Open implementation steps
- [x] Incoming unconsumed View Once media uses an explicit consume link.
- [x] Rendering alone does not request the one-time media endpoint.
- [x] Already consumed incoming media is shown as consumed and cannot be reopened.
- [x] Outgoing View Once media is shown as sent and is not offered for reopening.
- [ ] Manual runtime verification with real incoming and outgoing one-time media.

## Acceptance Criteria
- [ ] Rendering does not consume one-time media; explicit user action does, and non-reopenable states stay non-reopenable.

## Verification
Source/API contract audit completed. Runtime/UI verification remains manual by design; no browser/E2E CI is required.

## Relevant commits
PR #69 Messaging migration and View Once state completion commits.

## PR / review notes
No active review thread.

## Result
IMPLEMENTED, NOT VERIFIED.
