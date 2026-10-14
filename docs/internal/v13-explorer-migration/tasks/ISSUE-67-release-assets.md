# Issue #67 — Release package contains all V13 assets

- **Status:** IMPLEMENTED, NOT VERIFIED
- **Issue:** #67
- **Goal:** Package every nested V13 Explorer JS/CSS asset and assert release contents.

## Open implementation steps
- [x] Nested V13 package-data patterns added to `pyproject.toml`.
- [x] Existing package contract covers the nested package-data declarations.
- [x] `tools/build_release.py` now validates the completed ZIP against the complete selected release file set.
- [x] The release builder explicitly requires the V13 template, CSS entrypoints and `explorer/js/bootstrap.js` and compares every tracked `ladies/static/explorer/` asset with the archive contents.
- [ ] Execute a fresh release build from the migration head and record the successful archive verification.

## Acceptance Criteria
- [ ] A built release from the migration head includes every required V13 asset, not just source/package declarations.

## Verification
The release builder now performs the archive-content check itself. Source verification is complete; one actual release build from the migration head remains required before this issue can be marked VERIFIED.

## Relevant commits
PR #69 packaging migration commits, including `f79c96a` for archive/V13 asset validation.

## PR / review notes
No Manager packaging behavior is changed beyond validating that the existing release file set contains the V13 Explorer runtime.

## Result
IMPLEMENTED, NOT VERIFIED.
