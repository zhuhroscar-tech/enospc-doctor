# Changelog

All notable changes to `enospc-doctor` are documented here.

## v0.1.11 - 2026-09-25

- Made release-tag validation explicit so `v*` tags run the same tests, build, `.pyz` smoke check, and checksum generation as main-branch pushes.
- Added package project URLs for the changelog, homepage, and issue tracker.
- Ignored local build/smoke directories used during release verification.

## v0.1.10 - 2026-09-24

- Added this changelog and repository-contract coverage so release history, license links, CI wiring, CodeQL, and release artifacts stay visible and testable.
- Kept the runtime behavior unchanged; this is a source-quality and documentation maintenance release.

## v0.1.9 - 2026-09-24

- Modernized package license metadata to the current SPDX format.
- Added regression coverage for package metadata and version consistency.

## v0.1.8 - 2026-09-20

- Strengthened package/release validation around the standalone `.pyz`, wheel, source distribution, and checksums.

## v0.1.7 - 2026-09-13

- Fixed reserved-block permission-denied handling so an unavailable ext-filesystem check is not misreported as a genuine full-filesystem diagnosis.

## v0.1.6 - 2026-09-13

- Refined ENOSPC diagnosis output and test coverage for Linux filesystem edge cases.

## v0.1.5 - 2026-09-13

- Improved report stability and regression coverage for mount-level findings.

## v0.1.4 - 2026-09-12

- Fixed deleted-open-file reporting so `lsof` size and PID fields are not confused.

## v0.1.3 - 2026-09-12

- Expanded CLI and parser coverage for real-world command output shapes.

## v0.1.2 - 2026-09-12

- Fixed cross-mount deleted-file evidence leakage so findings remain scoped to the affected mount.

## v0.1.1 - 2026-09-11

- Added the shared restrained CLI design system used across the doctor tools.

## v0.1.0 - 2026-09-10

- Initial public release of the read-only ENOSPC diagnosis CLI.
