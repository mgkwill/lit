# OpenLit release notes

## 0.1.0 — unreleased

This is the first OpenLit release line. The executable remains `lit`.
OpenLit is based on the MIT-licensed Lit snapshot `98c59bb`, with independent
implementation and regression work. It is not a relicense of later upstream code.

The recovery work covers batch rollback and interrupted transactions, merge
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

Public release signing, final signed-artifact testing, and publication approval
are still pending. See [release requirements](docs/RELEASING.md).
