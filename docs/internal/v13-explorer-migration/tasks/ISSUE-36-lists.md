# Issue #36 — Likes, favorites and dislikes lists work

- **Status:** IMPLEMENTED, NOT VERIFIED
- **Issue:** #36
- **Goal:** Preserve productive LIKE/FAVORITE/DISLIKE lists, status, identity/relisting context and profile opening.

## Open implementation steps
- [x] V13 list screen and productive API flow implemented.
- [x] Favorite/Like/Dislike mutations stay coherent with the currently visible list.
- [x] Listing status and individual/all status refresh are exposed.
- [x] Known-person and recent new-listing state is shown from productive listing/change data.
- [x] List cards open the shared V13 profile implementation.
- [ ] Manual runtime verification with representative list data.

## Acceptance Criteria
- [ ] All three tabs, status/identity context and profile opening work in the running Explorer.

## Verification
Source/API contract audit completed. Runtime/UI verification remains manual by design; no browser/E2E CI is required.

## Relevant commits
PR #69 Profile/List migration and state-coherency commits.

## PR / review notes
No active review thread.

## Result
IMPLEMENTED, NOT VERIFIED.
