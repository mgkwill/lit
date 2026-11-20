# How OpenLit fits together

OpenLit has a Rust command-line program and separate Python tools for hosted
agent coordination. The program is installed as `lit`.

## Local repository

A `.lit` directory holds objects, the staging index, branch references, and
operation state. Files outside it are the working copy. Staging records file
contents for the next commit; a branch names a commit in history.

| Code | Responsibility |
| --- | --- |
| `src/main.rs` | Parse command-line arguments and return responses |
| `src/commands/` | Run repository operations |
| `src/core/` | Objects, references, merges, and repository discovery |
| `src/storage/` | Index, loose objects, and packs |
| `src/crypto/` | Hashes, signatures, encryption, and key accounting |
| `src/network/` | Native transports and transport policy |
| `src/ontology/` | Command discovery and JSON schemas |
| `src/formatter.rs` and `src/response.rs` | Output formats and response types |

Supported writes use repository locks and operation journals. A transaction
checkpoint allows rollback after interruption; readers may still observe
intermediate state. Repository verification checks reachable object links and
stored objects, including packs. It is a consistency check, not a backup.

Git import/export maps supported native history to Git. Authenticated Git remotes
use the system Git executable and its credentials. A native `.lit` store is not
a Git worktree; use the conversion commands at that boundary.

## Hosted workers

Each attempt has a pinned base and separate files. A scheduler assigns tasks.
The broker authenticates workers, checks the current assignment generation, and
publishes proposals with its own GitHub credential. Workers receive read tokens,
not write credentials.

The coordinator freezes proposal identity and admits it only when the required
Actions preflight passes. GitHub tests the exact integration candidate through
its protected merge queue. An integration receipt then connects the task, PR,
Git merge, and Actions evidence. A reported merge can precede receipt verification.

The broker's SQLite journal, replication, and quorum tools support recovery.
Recovery has explicit ownership checks; copying a database to a second machine
does not itself create a safe second writer. See [operations](tools/deployment/README.md).

## Boundaries

Workspaces separate files; sandboxes restrict processes; broker credentials
restrict publication; branch rules restrict merging. Each solves a different
problem. Keep those boundaries explicit in tests and deployment claims.
