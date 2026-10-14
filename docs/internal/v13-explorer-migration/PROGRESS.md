# V13 Explorer Migration Progress

Branch: `feature/v13-explorer-migration`  
PR: #69  
Tracker: #23

## Current

- V13 is the sole Explorer runtime; no legacy Explorer fallback remains.
- Source/API parity audit is complete for the planned functional areas. The audit closed concrete gaps in state synchronization, encounter value contracts, chat pagination/presence/replies/View Once/reactions, multiple WhatsApp endpoints, contact assignment, media/voice handling, manual pricing, profile sharing and settings/default-filter behavior.
- Development CI is intentionally limited to lightweight static syntax/metadata checks only.
- Browser/Playwright CI, platform matrices and their CI-only test scaffolding are retired and must not be reconstructed.
- #26–#36 and #38–#59 remain implemented at varying verification depth; final Explorer behavior is verified manually by design.
- Release builds now self-validate the completed ZIP against the selected release file set and explicitly require the V13 template/runtime entrypoints plus every tracked `ladies/static/explorer/` asset.

## Next

- Complete manual runtime verification for the remaining functional migration items #26–#36 and #38–#59 on the actual Explorer runtime/device/browser.
- #67 — execute one fresh release build from the migration head and record the successful archive/V13 asset validation.
- #68 — install that freshly built package into a clean target, start through the supported Launcher/Manager path and verify both V13 Explorer loading and unchanged Manager behavior.

## Blocked / external verification

- #23 — migration DoD remains open only until the remaining manual functional and release acceptance items are verified.
- #67/#68 require an actual release build/install run; source changes alone are intentionally not sufficient to mark them VERIFIED.

## Done

- #24 — V13 shell/state/router/API architecture is present.
- #25 — V13 is the sole Explorer template/runtime entrypoint.
- #37 — swipe and standalone modes share the same profile implementation.
- #60 — obsolete legacy Explorer JS/CSS files are removed.
- #61 — final Explorer template loads only V13 runtime assets.
- #62 — Manager runtime/UI files remain outside the Explorer migration scope.
- Obsolete DoD items #63–#66 (browser smoke, E2E, visual regression and heavyweight CI gate) were retired by design.
- No currently known source-level functional parity gap remains from the V13 execution plan; remaining uncertainty is runtime/manual or release-package verification.

## Audit notes

- PR #69 remains the only migration PR; base `main`, head `feature/v13-explorer-migration`; it remains open/draft and must not be auto-merged.
- Development CI must remain lightweight and non-runtime: no application start/import, HTTP checks, browser automation or platform/compatibility matrices.
- Release validation remains isolated from development CI. The release builder itself validates archive completeness; fresh install/start remains a release-acceptance activity.
