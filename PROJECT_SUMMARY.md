# OpenLit at a glance

OpenLit is a version-control tool for people and coding agents. The executable
is `lit`. [Version 0.1.1](https://github.com/mgkwill/lit/releases/tag/v0.1.1)
is published with updated copyright and attribution in the archives.

See [project attribution](NOTICE.md) for the original MIT source and contributor
notices.

The work focuses on reliable JSON operations, rollback and crash recovery,
Git interoperability, separate agent workspaces, and controlled publication
through GitHub Actions and a protected merge queue. The broker and scheduler
are separate tools; installing the CLI does not deploy those services.

Native candidate tests cover Linux x86-64 and arm64, macOS Intel and Apple
Silicon, and Windows x86-64. Platform limits and exact evidence are in
[installation](docs/INSTALL.md) and [testing](TESTING.md).

Full upstream parity and blanket security certification are not claimed. See [the release checklist](docs/RELEASING.md) and
[the roadmap](ROADMAP.md) for what remains.
