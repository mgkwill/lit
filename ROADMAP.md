# What is next for OpenLit

Version 0.1.1 is published with updated copyright and attribution in every
archive. We prioritize agent workflows over a checklist of every upstream feature.

## Release preparation

- [x] Publish the first release with Minisign signatures and verified downloads.
  [Publication record](audits/publication-0.1.0-2026-11-21.md).
- [x] Update contributor attribution and include `NOTICE.md` in each archive.
- [x] Add regression checks for missing or altered archive notices.
- [x] Build and test 0.1.1 on all five native targets from the current source.
- [x] Sign the fresh archives through the approval-protected publisher workflow
  and independently verify signatures and tamper rejection.
- [x] Obtain owner approval, publish the exact 0.1.1 candidate, and verify all
  public downloads. [Publication record](audits/publication-0.1.1-2026-11-22.md).

[Verified 0.1.1 candidate](audits/release-0.1.1-2026-11-22.md) records the signed
source and completed checks. The signing workflow cannot publish a release.
Apple notarization, Windows Authenticode, and GitHub attestations remain optional
future work.
See [release requirements](docs/RELEASING.md).

## Next work

Use actual user failures to choose the next work. Good candidates are simpler
setup for broker deployments, clearer recovery diagnostics, dependency maintenance,
and wider platform coverage. Keep bounded inference concurrency; simulated large
cohorts and smaller real-agent runs answer different questions.

Native issue tracking, federation, trust scores, generated descriptions, and a
passphrase daemon are deferred. Use Git for history conveniences where it already
fits. A feature is not required merely because an upstream help page lists it.

Historical results remain under `tests/fixtures/`. A completed test at an older
commit does not automatically qualify a new release or a new operating system.
