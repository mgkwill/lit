# OpenLit at a glance

OpenLit is a version-control tool for people and coding agents. The executable
is `lit`; the first OpenLit release is being prepared as 0.1.0.

The project starts from the MIT-licensed Lit snapshot `98c59bb` and independent
recovery work. Later upstream versions are black-box references, not source
inputs. The original MIT attribution remains in [LICENSE](LICENSE).

The work focuses on reliable JSON operations, rollback and crash recovery,
Git interoperability, separate agent workspaces, and controlled publication
through GitHub Actions and a protected merge queue. The broker and scheduler
are separate tools; installing the CLI does not deploy those services.

Native candidate tests cover Linux x86-64 and arm64, macOS Intel and Apple
Silicon, and Windows x86-64. Platform limits and exact evidence are in
[installation](docs/INSTALL.md) and [testing](TESTING.md).

Full upstream parity, blanket security certification, and a public signed release
are not claimed. See [the release checklist](docs/RELEASING.md) and
[the roadmap](ROADMAP.md) for what remains.
