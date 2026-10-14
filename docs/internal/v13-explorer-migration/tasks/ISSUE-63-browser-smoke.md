# Issue #63 — Browser smoke tests pass

- **Status:** PARTIAL
- **Issue:** #63
- **Goal:** Browser smoke for boot, search/filter, discover, profile, lists, messages/chat and settings, including Firefox-relevant behavior.

## Open implementation steps
- [x] Chromium boot/search/swipe/grid/profile partial test exists.
- [ ] Cover Favorites/Lists.
- [ ] Cover Messages and Chat.
- [ ] Cover Settings.
- [ ] Run on Firefox as well as Chromium.
- [ ] Execute browser smoke in canonical CI with a pinned/reproducible browser-test dependency.

## Acceptance Criteria
- [ ] Required screens smoke successfully in Chromium and Firefox.
- [ ] Browser checks are actually executed by CI.

## Verification
`tests/browser/explorer.mjs` currently covers only a subset and is not invoked by `.github/workflows/ci.yml`.

## Relevant commits
- `ae665b7` — partial Chromium smoke/E2E test.

## PR / review notes
No active review thread.

## Result
PARTIAL — current work item.
