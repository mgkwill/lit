# What is next for OpenLit

The current goal is an honest, usable 0.1.0 release of the MIT-based recovery.
We prioritize agent workflows over a checklist of every upstream feature.

## Release preparation

- [x] Use the OpenLit product and package name while keeping `lit` at the command line.
- [x] Rewrite active guides in plain language and check the examples against the binary.
- [x] Rebuild the renamed version on all five native targets and save exact evidence.
- [ ] Review dependency notices and final source provenance. Archive reviewed
  implementation records with the manifest required by AGENTS.md.
- [ ] Choose publisher identities, implement signing and attestations, and test the
  final signed archives on clean machines.
- [ ] Present the exact release to the owner and ask before tagging or publishing.

[Candidate evidence](tests/fixtures/release-qualification/2026-11-20-openlit-notices.md)
records the exact tested sources and remaining limits.

The publication workflow is disabled while those requirements remain open.
See [release requirements](docs/RELEASING.md).

## After the first release

Use actual user failures to choose the next work. Good candidates are simpler
setup for broker deployments, clearer recovery diagnostics, dependency maintenance,
and wider platform coverage. Keep bounded inference concurrency; simulated large
cohorts and smaller real-agent runs answer different questions.

Native issue tracking, federation, trust scores, generated descriptions, and a
passphrase daemon are deferred. Use Git for history conveniences where it already
fits. A feature is not required merely because an upstream help page lists it.

Historical results remain under `tests/fixtures/`. A completed test at an older
commit does not automatically qualify a new release or a new operating system.
