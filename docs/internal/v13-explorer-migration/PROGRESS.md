# V13 Explorer Migration Progress

Branch: `feature/v13-explorer-migration`  
PR: #69  
Tracker: #23

## Current

- Development CI has been intentionally reduced to lightweight static syntax checks only.
- Browser/Playwright CI, platform matrices and their CI-only test scaffolding have been removed.
- #26–#36 and #38–#59 remain implemented at varying verification depth; manual verification is used for Explorer behavior.

## Next

- Complete manual verification for remaining functional migration items #26–#36 and #38–#59.
- #67 — verify the built release archive contains every V13 asset.
- #68 — validate install/start from a freshly built release package.

## Blocked

- #23 — migration DoD remains open until the remaining functional and release acceptance items are verified.

## Done

- #24 — V13 shell/state/router/API architecture is present.
- #25 — V13 is the sole Explorer template/runtime entrypoint.
- #37 — swipe and standalone modes share the same profile implementation.
- #60 — obsolete legacy Explorer JS/CSS files are removed.
- #61 — final Explorer template loads only V13 runtime assets.
- #62 — Manager runtime/UI files remain outside the Explorer migration scope.
- Obsolete DoD items #63–#66 (browser smoke, E2E, visual regression and heavyweight CI gate) were retired by design.

## Audit notes

- PR #69 remains the only migration PR; base `main`, head `feature/v13-explorer-migration`.
- Development CI must remain lightweight and non-runtime: no application start/import, HTTP checks, browser automation or platform/compatibility matrices.
- Release validation remains isolated in the release workflows.
