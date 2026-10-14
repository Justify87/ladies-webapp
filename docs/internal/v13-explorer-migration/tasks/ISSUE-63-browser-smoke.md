# Issue #63 — Browser smoke tests pass

- **Status:** IMPLEMENTED, NOT VERIFIED
- **Issue:** #63
- **Goal:** Browser smoke for boot, search/filter, discover, profile, lists, messages/chat and settings, including Firefox-relevant behavior.

## Open implementation steps
- [x] Cover Search/filters, Swipe, Grid and Profile.
- [x] Cover Favorites/Lists.
- [x] Cover Messages and Chat.
- [x] Cover Settings.
- [x] Run the same smoke contract on Chromium and Firefox.
- [x] Add browser smoke as a canonical CI matrix job with pinned Playwright `1.64.0`.
- [ ] Confirm the new CI jobs pass on the pushed head.

## Acceptance Criteria
- [ ] Required screens smoke successfully in Chromium and Firefox on current PR head.
- [x] Browser checks are actually executed by CI rather than existing as dormant files.

## Verification
Implementation is now wired into `.github/workflows/ci.yml`; final status waits for the first resulting Actions run.

## Relevant commits
- `ae665b7` — initial partial Chromium browser coverage.
- Pending current #63 implementation commit — Firefox + missing screens + CI matrix.

## PR / review notes
No job/test/matrix was removed or weakened; the browser matrix adds Chromium and Firefox verification.

## Result
IMPLEMENTED, NOT VERIFIED pending CI.
