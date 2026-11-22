# OpenLit

![OpenLit: work in parallel, keep a clear history.](docs/images/openlit-header.png)

OpenLit is version control for people and coding agents. It saves file history,
returns JSON that programs can read, and lets separate workers propose changes
without sharing a working directory.

[OpenLit 0.1.1](https://github.com/mgkwill/lit/releases/tag/v0.1.1) is available
for Linux, macOS, and Windows. Its archives include the updated copyright and
project attribution. Verify downloads with [Minisign](docs/RELEASE_CREDENTIALS.md)
before extracting or running them.

## Start here

Download and verify an archive using the [installation guide](docs/INSTALL.md),
or build from this checkout with Rust and a C compiler:

```sh
cargo build --locked --release
./target/release/lit --version
cargo install --locked --path . --bin lit
```

On Windows, the executable is `target\release\lit.exe`. Install Git as well
if you will use Git remotes. See [installation](docs/INSTALL.md) for platform
requirements. The package is not published to crates.io; use a release archive
or install from this checkout.

Create a small repository:

```sh
mkdir example
cd example
lit init
printf 'hello\n' > hello.txt
lit add hello.txt
lit commit -m "Save the first file"
lit status
```

Commands return JSON by default. Use `lit status --human` for a readable view.
See the [quick start](QUICKSTART.md) for branches and recovering from mistakes.

## What you can do

| Need | OpenLit support |
| --- | --- |
| Save and review work | Commits, branches, diffs, merge conflicts, and merge abort |
| Drive it from a program | JSON output, JSONL batches, command schemas, and MCP |
| Recover failed writes | Transactions, batch rollback, and repository verification |
| Work with Git | Import, export, clone, fetch, pull, and push; authenticated Git HTTPS and SSH |
| Run workers separately | Checkouts pinned to a full commit ID on macOS and Linux |
| Control shared publication | A separate authenticated broker and GitHub's protected merge queue |
| Store encrypted history | Encrypted objects and packs, key rotation, and per-key usage accounting |

For a batch, send one JSON object per line:

```sh
printf '%s\n' \
  '{"command":"add","args":{"files":["hello.txt"]}}' \
  '{"command":"commit","args":{"message":"Save the edit"}}' \
  | lit batch --rollback-on-error
```

This mode restores the index and branch tip if an operation fails. Working
files remain under your control. After a process crash, run `lit tx rollback`
before another write. `--atomic` only stops on an error; it does **not** undo
earlier writes. Read [batch and transaction details](EXAMPLES.md).

## Working with other agents

Give each attempt its own checkout and a fixed starting commit. Let the
scheduler assign ownership. Send proposed changes through the broker; let
GitHub test the proposed merge before it updates the shared branch.

The **Actions-only rule** means the required check must come from GitHub
Actions on the exact merge candidate. A manually posted status cannot replace
it. The sandbox also requires a pull request and merge queue, with no bypass
actors. See [the agent workflow](tools/README.md).

A hundred queued attempts do not need a hundred simultaneous model sessions.
The controller limits active coding sessions. Our lab profiles use inference
on host-a, with workers on host-a, host-e, and host-f.

## Limits to know

- This is an early release. Back up repositories before migration and keep the
  original until you have checked the copy.
- Windows builds are tested, but isolated workspaces and native repository
  migration currently require macOS or Linux.
- Strict sandbox mode refuses to start a command if isolation is unavailable.
  Windows has no strict confinement backend. Default sandbox mode is not a
  security boundary.
- Git interoperability uses the system Git executable. It does not make a
  `.lit` directory interchangeable with a `.git` directory.
- OpenLit is not a FIPS-validated cryptographic module. Encryption does not
  encrypt working files, and release signing is separate from commit signing.

## Guides

- [Quick start](QUICKSTART.md) · [Examples](EXAMPLES.md) · [Installation](docs/INSTALL.md)
- [Agent workflow](tools/README.md) · [Broker operations](tools/deployment/README.md)
- [Security](SECURITY.md) · [Encryption](ENCRYPTION.md) · [Offline work](AIRGAP.md)
- [Architecture](ARCHITECTURE.md) · [Command discovery](ONTOLOGY.md)
- [Testing](TESTING.md) · [Release requirements](docs/RELEASING.md) · [Next work](ROADMAP.md)
- [All guides](docs/README.md)

## License

OpenLit is maintained by Marcus G.K. Williams and licensed under the
[MIT license](LICENSE). See [project attribution](NOTICE.md) for its origin
and copyright credits. Read [AGENTS.md](AGENTS.md) before contributing.
