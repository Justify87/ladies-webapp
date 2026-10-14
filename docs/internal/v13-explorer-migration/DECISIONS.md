# V13 Explorer Migration Decisions

## Binding decisions

1. Work remains exclusively in `Justify87/ladies-webapp` on `feature/v13-explorer-migration`; PR #69 is reused and is not auto-merged.
2. V13 fully replaces the Explorer presentation layer. No permanent legacy fallback or parallel Explorer runtime is accepted.
3. The prototype is a design/architecture reference only. Prototype fixtures, demo chat data, dummy QR values and invented product data are not production sources.
4. Backend/API data remains source of truth for search catalogs, filters, profiles, messaging, pricing, settings and history.
5. UI state follows API → adapters/view models → commands/effects → reducer/store → selectors → renderer → DOM; DOM is output, not primary state.
6. Swipe and standalone profiles share one profile implementation.
7. Messaging keeps the productive feature surface; the prototype demo chat is not a replacement contract.
8. Pricing is integrated inline with the message that produced the price signal/proposal.
9. Ladies Manager is out of migration scope and must remain functionally unchanged.
10. Development CI is intentionally minimal and static only: Python compilation, Explorer JavaScript syntax, and TOML/JSON syntax. It must not import or start the application, open HTTP endpoints, launch browsers, run E2E/visual tests, or maintain compatibility/platform matrices.
11. Release workflows are separate from development CI and only prepare version metadata, build the release artifact, verify release metadata/archive integrity, tag and publish the release.
12. Functional UI verification is performed manually during development; no browser-smoke, E2E or visual-regression CI gate is part of the V13 migration contract.
13. `VERIFIED` is used only when concrete source/diff/manual/release evidence exists; implementation alone remains `IMPLEMENTED` until verification is recorded.
