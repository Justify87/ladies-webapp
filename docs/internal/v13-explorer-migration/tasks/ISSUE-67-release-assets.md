# Issue #67 — Release package contains all V13 assets

- **Status:** IMPLEMENTED, NOT VERIFIED
- **Issue:** #67
- **Goal:** Package every nested V13 Explorer JS/CSS asset and assert release contents.

## Open implementation steps
- [x] Nested V13 package-data patterns added to `pyproject.toml`.
- [x] Existing test contract asserts nested package-data patterns.
- [ ] Build the release archive and verify the complete V13 asset set inside it.

## Acceptance Criteria
- [ ] Built release includes every required V13 asset, not just source-package declarations.

## Verification
Source/package-data evidence exists; actual archive verification remains.

## Relevant commits
PR #69 packaging migration commits.

## PR / review notes
No Manager packaging behavior should be altered except what is necessary to include V13 Explorer assets.

## Result
IMPLEMENTED, NOT VERIFIED.
