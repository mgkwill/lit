# OpenLit release notes

## 0.1.1 — 2026-11-22

- Include the updated MIT copyright notice for Marcus G.K. Williams while
  preserving Nervosys attribution.
- Include `NOTICE.md` in every native archive and verify it against the build
  source before running the extracted executable.
- Build and sign from the current privacy-redacted source history. Previous
  0.1.0 signatures remain historical evidence for their original build.

[OpenLit 0.1.1 is published](https://github.com/mgkwill/lit/releases/tag/v0.1.1)
from signed source `777acec`. All 15 public assets match the approved candidate.
See the [qualification evidence](audits/release-0.1.1-2026-11-22.md) and
[publication record](audits/publication-0.1.1-2026-11-22.md).

## 0.1.0 — 2026-11-21

This is the first OpenLit release. Run it with `lit`.

This release includes batch rollback and interrupted transactions, merge
integrity and abort, packed objects and verification, encrypted packing and key
rotation, Git round trips and remotes, and strict sandbox refusal when isolation
is unavailable. Separate broker tools add task ownership, reassignment checks,
queued publication, verified integration receipts, and recovery support.

The product and Cargo package are named `openlit`. Archives will use that name,
but existing repository paths, environment variables, and MCP tool names remain
compatible. The guides have been rewritten in plain language.

Native candidate builds cover Linux x86-64 and arm64 on glibc 2.35, macOS Intel
and Apple Silicon, and Windows x86-64. Windows strict sandbox mode refuses to
launch because it has no confinement backend. Isolated workspaces and native
repository migration currently require macOS or Linux.

[OpenLit 0.1.0 is published](https://github.com/mgkwill/lit/releases/tag/v0.1.0)
from tested source `67cbd62`. It passed approval-protected publisher signing and
final signature, tamper-rejection, and archive checks. Public downloads were
verified against those exact bytes. See the
[signing evidence](audits/ci-publisher-signing-2026-11-21.md) and
[publication record](audits/publication-0.1.0-2026-11-21.md).
