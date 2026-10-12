# Ladies Explorer V9.6 – Refactoring contract

Status: in progress. Baseline: V9.5.x main. This is an architecture refactor, not a feature release.

## Non-negotiable invariants

- Keep Flask, SQLAlchemy, SQLite/WAL, vanilla JavaScript and the Baileys Node worker.
- Preserve person-centric identity, listing history, media archives, likes/favorites and existing SQLite data.
- Preserve WhatsApp session persistence, PN/LID identity, multi-number threads, replies, reactions, edits/deletes, view-once and last-10 recent-history rehydration.
- Preserve pricing proposals, revisions, evidence, OCR and optional model integration.
- Do not reset user data or silently change API behavior. Existing V9.5 databases must upgrade without data loss.
- Keep a single launcher and mobile-first LAN operation.

## Ordered implementation slices

1. Baseline characterization and public-contract tests; replace tests coupled to source strings/version aliases.
2. Shared application context: one database engine/schema initialization, explicit model registration; centralized version/config.
3. Request validation, typed application errors, request IDs and privacy-preserving logs.
4. Messaging consolidation: one canonical service and routes; extract gateway, media store and recent-history cache; remove runtime/complete/reviewfix inheritance and aliases only after parity tests.
5. Worker hardening: media-root validation and ordered state-mutating commands.
6. Split core repository into person/search/history/settings/archive domains; move search/profile orchestration out of HTTP routes.
7. One backend filter catalog and frontend configuration contract.
8. Replace frontend monkeypatch/bridge layers with explicit native ES modules; consolidate CSS; preserve current UI and gestures.
9. Consolidate pricing hardened service and media locator without changing proposal transaction semantics.
10. Separate pairing secret from device sessions; bound/deduplicate background jobs; streaming/size-limited media proxy and security headers.
11. Unify dependency/bootstrap ownership, strengthen Python/JS/Node CI and align README/version display.
12. Delete obsolete compatibility code; verify full regression suite, fresh and upgraded database, browser and real-phone WhatsApp flows before merge.

## Gates for every slice

- Changes are incremental and independently reviewable; no new product features.
- Tests and static checks pass before dependent slices proceed.
- No reduction in security checks or functional behavior.
- Avoid adding abstractions solely to match a diagram; use the smallest useful modules.
- Never merge the complete refactor before automated gates and documented manual smartphone/WhatsApp acceptance.

## Release acceptance

No versioned service inheritance, `reviewfix`/`complete` layers, `service_v92`/`web_v92` aliases, frontend global monkeypatch layers, or duplicate filter/version catalogs remain. Exactly one initialized application database; sensitive logs are redacted; browser errors do not expose exception details; media paths are root-validated. CI validates all JavaScript and public service/API contracts. README reflects the implemented architecture.
