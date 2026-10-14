# Issue #25 — V13 is the only Explorer UI

- **Status:** VERIFIED
- **Issue:** #25
- **Goal:** V13 must be the sole Explorer presentation/runtime.

## Open implementation steps
- [x] Replace Explorer template with V13 shell.
- [x] Remove legacy runtime loading.

## Acceptance Criteria
- [x] No parallel/legacy Explorer runtime is loaded.

## Verification
`ladies/templates/index.html` loads only V13 Explorer CSS/modules; consolidation tests pass in CI.

## Relevant commits
PR #69 atomic cutover migration commits.

## PR / review notes
No active review thread.

## Result
VERIFIED.
