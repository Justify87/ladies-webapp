# Issue #50 — Messaging live updates work

- **Status:** IMPLEMENTED, NOT VERIFIED
- **Issue:** #50
- **Goal:** Preserve SSE-driven live messaging/status/presence updates without DOM observer bridges.

## Open implementation steps
- [x] EventSource integration updates the Explorer through effects/store.
- [x] Presence events are mapped to thread state and rendered as typing/recording/online state.
- [x] Presence updates avoid unnecessary full thread reloads and expire stale transient states.
- [x] Non-presence live refreshes preserve already loaded chat history.
- [ ] Manual live integration verification against a connected WhatsApp session.

## Acceptance Criteria
- [ ] Live events update state through effects/store without DOM-as-state hacks in the running Explorer.

## Verification
Source/backend event-contract audit completed. Live runtime verification remains manual by design; no browser/E2E CI is required.

## Relevant commits
PR #69 Messaging migration and live-state hardening commits.

## PR / review notes
No active review thread.

## Result
IMPLEMENTED, NOT VERIFIED.
