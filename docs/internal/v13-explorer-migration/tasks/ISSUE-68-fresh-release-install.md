# Issue #68 — Fresh release installation works

- **Status:** OPEN, SOURCE-AUDITED
- **Issue:** #68
- **Goal:** Validate install/start from a freshly built release package with V13 Explorer and existing Manager/runtime.

## Open implementation steps
- [x] Audit the packaged install/start path for V13-specific source-level blockers.
- [x] Confirm `core.install()` copies the complete release tree into the versioned release directory before activation.
- [x] Confirm root launchers still enter through the existing Launcher Manager and the Explorer starts from the installed release entrypoint.
- [ ] Build a fresh release from the migration head.
- [ ] Install from that release package into a clean target.
- [ ] Start through the supported launcher/Manager path.
- [ ] Verify V13 Explorer loads and Manager behavior is unchanged in the running packaged installation.

## Acceptance Criteria
- [ ] Fresh packaged install starts successfully with all V13 assets present.

## Verification
Source audit of `packaging/install.command`, `packaging/start.command`, `tools/launcher_core.py`, `tools/launcher_manager.py` and `ladies_explorer.py` found no V13-specific blocker in the install/start path. This does not replace the required fresh packaged runtime test.

## Relevant commits
PR #69 release/packaging migration commits; `f79c96a` adds archive/V13 asset validation to the release builder.

## PR / review notes
Must not merge PR #69 as part of this verification. Development CI must remain lightweight; fresh-install validation is release acceptance, not a development gate.

## Result
OPEN, SOURCE-AUDITED. Fresh packaged install/start verification still required.
