# Lit — The Agentic-First Distributed Version Control System

**The world's first version control system designed for AI agents first and humans second.**

Lit is a complete Git replacement written in Rust, offering local and remote distributed version control with post-quantum cryptographic security, structured machine-readable output, and native API server and MCP integration for autonomous agent workflows.

## Why Lit?

Git was designed in 2005 for human developers using terminals. Every interface — output formatting, error messages, interactive prompts, conflict markers — assumes a human is reading and responding. AI agents must parse Git's freeform text output with fragile regex, work around interactive prompts, and translate `<<<<<<<` conflict markers into something actionable.

**Lit inverts this.** Every command emits structured JSON by default. Errors include machine-actionable codes and remediation hints. Merge conflicts are structured objects, not text markers. Batch operations accept JSONL on stdin. Nothing ever prompts for input. Humans get the same power through a `--human` flag.

|                         | Git                        | Lit                                     |
| ----------------------- | -------------------------- | --------------------------------------- |
| **Designed for**        | Human developers           | AI agents (humans supported)            |
| **Default output**      | Freeform text              | Structured JSON                         |
| **Error handling**      | Freeform strings           | Typed error codes + remediation         |
| **Batch operations**    | Shell scripting            | Native JSONL batch mode                 |
| **API access**          | Third-party wrappers       | Built-in HTTP + MCP server              |
| **Merge conflicts**     | `<<<<<<<` markers          | Structured conflict objects             |
| **Agent metadata**      | Commit message conventions | First-class metadata field              |
| **Cryptography**        | SHA-1 / SHA-256            | SHA3-512 + BLAKE3 (quantum-resistant)   |
| **Signatures**          | GPG / SSH                  | ML-DSA (post-quantum, NIST FIPS 204)    |
| **Encryption**          | None built-in              | AES-256-GCM at rest, TLS 1.3 in transit |
| **Interactive prompts** | Frequent                   | Never (zero-prompt design)              |

## Installation

```bash
cargo build --release
cargo install --path .
```

## Quick Start

### For Agents (JSON output — default)

```bash
# Initialize
lit init
# → {"status":"ok","command":"init","path":"/path/to/repo"}

# Stage and commit
lit add src/
lit commit -m "implement feature X"
# → {"status":"ok","command":"commit","hash":"abc123...","tree":"def456..."}

# Check status
lit status
# → {"status":"ok","command":"status","branch":"main","staged":[],"modified":[],"clean":true}

# Batch operations via JSONL stdin
echo '{"op":"add","files":["src/a.rs"]}
{"op":"commit","message":"fix bug"}' | lit batch
```

Batch reads one operation per nonblank line. The canonical form is
`{"command":"status","args":{}}`. The flat `op` form above remains supported.
`--dry-run` checks command names and argument types without running repository
operations. Check each result and the top-level `failed` count even if the
process exits zero. Use `--fail-on-error` to return exit status 1 after
reporting any failed operations, including in dry-run. `--atomic` stops at the
first error and retains earlier writes.

Use `lit batch --rollback-on-error` to restore the original index and branch tip
when a line fails. It supports `add`, `commit`, `snapshot`, `status`, `log`,
and branch listing on a checked-out branch. The response reports
`transaction: "committed"` or `transaction: "rolled_back"`. A rolled-back
batch exits 1 and marks earlier results `rolled_back`. Add `--dry-run` to
validate every line against this mode without starting a transaction.

The mode holds the repository write lock until completion. Working files
remain under the caller's control. Readers can see intermediate state, and
unreferenced objects created by a failed batch can be collected by `lit gc`.
After a process crash, run `lit tx rollback` to restore the checkpoint before
starting another write.

A transaction checkpoints the current branch, index, and files already tracked
when it starts. Supported writes are serialized across Lit processes. If
`lit tx rollback` is interrupted, run it again to resume before making further
Lit writes. Rollback leaves files that were untracked at the start alone.
Edits made directly to tracked files
during the transaction can be replaced by the checkpoint on rollback.

### For Humans

```bash
# Use --human or -H for familiar output
lit status --human
lit log --human
lit diff --human

# Or set globally
export LIT_OUTPUT=human
```

## Core Commands

### Version Control

```bash
lit init [--bare] [path]           # Initialize repository
lit add <files...>                 # Stage files
lit commit -m <message>            # Record changes
lit status                         # Show working tree status
lit log [--count N]                # Show commit history
lit diff [--word-diff] [--stat]    # Show changes (structured hunks)
lit show <object>                  # Inspect any object
lit branch [name] [--delete]       # Manage branches
lit checkout <target> [-b]         # Switch branches
lit merge <branch>                 # Merge branches (3-way)
lit merge --abort                  # Restore files and index entries changed by an active merge
lit resolve <file> --strategy=X    # Programmatic conflict resolution
lit tag <name> [--sign]            # Create tags
lit stash [push|pop|list]          # Stash work-in-progress
lit reset [--soft|--mixed|--hard]  # Move HEAD
lit revert <commit>                # Create inverse commit
lit cherry-pick <commit>           # Apply specific commits
lit rebase <base>                  # Replay commits
lit blame <file>                   # Line-by-line authorship
lit bisect <start|good|bad|reset>  # Binary search for bug-introducing commit
lit reflog                         # Reference update history
```

During a conflicted merge, Lit applies non-conflicting changes to the worktree
and index while leaving HEAD at the original commit. Choose a side with
`lit resolve <file> --strategy ours|theirs`, then run `lit resolve --continue`.
Use `lit merge --abort` (or `lit resolve --abort`) to restore merge-owned paths
and keep unrelated work. If you edited a merge-owned path, save that edit
elsewhere and restore the merge version before retrying abort. A merge started
by a version without an abort checkpoint can still be resolved and continued.


### Remote Operations

```bash
lit remote add <name> <url>        # Add remote
lit push <remote> <branch>         # Push to remote
lit pull <remote> <branch>         # Fetch and merge
lit clone <url> [directory]        # Clone repository
lit fetch <remote>                 # Download without merging
```

Supported transports: HTTPS, SSH, `lit://`, `file://`, USB/removable media, network shares.

### Agentic Commands

```bash
lit batch                          # Execute JSONL operations from stdin
lit serve --port 3000              # Launch HTTP API server
lit mcp-serve                      # Launch MCP tool server for LLM agents
lit tx <operations...>             # Atomic transaction mode
lit snapshot -m <msg>              # Add-all + commit in one step
lit search <query>                 # Full-text search across history
lit watch                          # Emit filesystem change events
lit swarm <register|lease-list>    # Agent registrations and advisory file leases
lit ontology                       # Output command ontology for agent discovery
lit schema [--command <id>]        # Generate JSON Schema (draft 2020-12) from ontology
```

Swarm leases coordinate Lit processes sharing one repository. Use paths
relative to its root. The first successful lease write imports legacy lease
records into the new shared state. Use current clients for subsequent lease
operations in that repository.

### Maintenance & Migration

```bash
lit verify                         # Full repository integrity check
lit gc                             # Garbage collection — pack loose objects
lit lfs track <pattern>            # Track large files via LFS
lit lfs migrate                    # Migrate existing large files to LFS
lit import-git <source>            # Import from Git repository
lit export-git <destination>       # Export to Git format
lit migrate-repo inspect <source>  # Verify that a native repository can be copied
lit migrate-repo copy <source> <destination>
lit migrate-repo rollback <original> <migrated> <archive>
lit rotate-key                     # Rotate encryption passphrase
lit config [get|set|list]          # Manage configuration settings
```

`lit gc` packs loose objects into a verified native pack before removing the
loose copies. Reads and `lit verify` include older plaintext v1 packs in
unencrypted repositories. In an encrypted repository, `lit gc` writes v2
packs with authenticated encryption for each compressed object payload.
Pack indexes expose object IDs and sizes. Supply the repository passphrase
with `--passphrase-file` for noninteractive reads, writes, and GC.

`lit rotate-key` re-encrypts loose and packed objects, the index, and any
already-encrypted refs. It keeps native text refs in their existing form.
For automation, pass the current credential with `--passphrase-file` and
set `LIT_NEW_PASSPHRASE_FILE` to a separate file. Interrupted publication
resumes with `lit rotate-key` using the new credential. Rotation installs
a repository-local key and preserves the old key file for other repositories
or backups that may still use it.

Lit records passphrase backoff and AES-GCM usage by key identity under
`~/.lit/key-state`. Processes and repository aliases that share that
directory share the counters. Failed guesses trigger backoff before Lit
runs the password derivation again. Lit reserves encryption slots before
use and discards unused reservations when a process exits. Keep this state
with the installation and rotate before writing with a legacy key or one
whose usage ledger is unavailable. A new key gets a new budget. Use separate
keys across hosts that cannot share one accounting authority.

On macOS and Linux, `migrate-repo` verifies working and bare native Lit
repositories, including v1.8.0 fixtures, using a private staging copy.
Stop writers to the source before inspection or migration. Use a trusted
destination parent and a new destination outside the source. Migration
checks the source and copy against a SHA-256 snapshot of paths, entry types,
ordinary POSIX modes, file contents, and symlink targets. It retains history,
refs, the index, working files, and local metadata, including LFS data already
present. Ownership, timestamps, ACLs, extended attributes, and hardlink
relationships are outside that snapshot. Symlinks inside `.lit`, special
files, and active operations are rejected.

The copy is verified before publication with an atomic no-replace rename.
It carries a version 1 receipt in `.lit/repository-format.json`. Recovery
commands accept unmarked repositories and reject unknown receipt versions.
After interruption, repeat the same `copy` command against the same source
snapshot to rebuild the private stage. Keep the original at its existing path
for rollback. `rollback` checks that the original still matches the receipt
and atomically moves the migrated repository, including subsequent work, to
a new sibling archive path. Resume work at the original path after rollback.

For encrypted sources, supply the current `--passphrase-file` and use a
repository-local key under `.lit`. Inspection reports whether rotation is
needed. For a legacy key or v1 encrypted pack, set `LIT_NEW_PASSPHRASE_FILE`
when running `copy`. The copy gets a managed key and v2 encrypted packs.
Migration verifies v1.8.0 encrypted ref tables and writes their entries as
individual encrypted refs on the copy. Conflicting packed and loose refs
block migration. The original keeps its packs, refs, key, and passphrase.

Local Git import and export require `git` on PATH and use SHA-1 repositories.
Import reads loose and packed objects and refs, preserving branches, tags and
symbolic or detached HEAD. It updates Lit history and refs. Checkout is a
separate operation. Export writes reachable history to a new or empty
directory as a bare Git repository and runs `git fsck --full --strict` before
publishing it. Git commits and annotated tags imported by this converter retain
their original object IDs on re-export.

Git remotes support local `file://` URLs and smart HTTP. Git clone imports
branches and tags, fetch updates branch tracking refs, and Git pull advances
the current branch only when it can fast-forward and refuses worktree
collisions, including file-to-directory replacements. Git push sends the selected
branch and delegates the fast-forward check to Git. The Git push result uses
zero for the object count because Git's porcelain output does not report it.

### Sandboxed execution

`lit sandbox run <name> --strict -- <command>` confines filesystem access
to the copied workspace and a read-only system runtime allowlist and denies
network access. On macOS it uses Seatbelt through the deprecated system
`sandbox-exec` launcher. On Linux x86-64 and arm64 it requires a trusted
non-setuid Bubblewrap in `/usr/bin` or `/bin`, working namespaces and
seccomp, and kernel support for `close_range` with `CLOSE_RANGE_CLOEXEC`.
The Linux workspace is mounted at `/workspace` inside strict runs.

Lit returns `SANDBOX_UNAVAILABLE` before the requested command starts if
strict isolation cannot be prepared. Default runs scrub the environment
and report filesystem and network isolation as unenforced.

## Structured Errors

All commands return typed `LitError` values with machine-actionable codes, not freeform strings:

```json
{
  "status": "error",
  "command": "commit",
  "code": "REPO_NOT_FOUND",
  "message": "Not a lit repository (or any parent up to /)",
  "suggestions": ["Run 'lit init' to create a new repository"]
}
```

Error codes: `ENCRYPTION_ERROR`, `IO_ERROR`, `CONFIG_ERROR`, `NETWORK_ERROR`, `REPO_NOT_FOUND`, `OBJECT_NOT_FOUND`, `INDEX_ERROR`, `MERGE_CONFLICT`, `AUTH_FAILURE`, `INVALID_INPUT`, `PERMISSION_DENIED`, `TIMEOUT`, `PROTOCOL_ERROR`, `INTERNAL_ERROR`, `GENERAL_ERROR`.

## JSON Schema

Generate standard JSON Schema (draft 2020-12) from the ontology for agent SDK discovery and input validation:

```bash
# Full schema — all types and command interfaces
lit schema

# Single command schema
lit schema --command commit
# → {"$schema": "https://json-schema.org/draft/2020-12/schema",
#    "input": {"properties": {"message": {"type": "string"}, ...}}}
```

The schema is auto-generated from the ontology — types map to `$defs`, commands map to input/output schemas with metadata (idempotent, safe, side_effects, preconditions).

## API Server

```bash
# Launch for agent swarm access
lit serve --port 3000 --token $LIT_TOKEN
```

```bash
# Agents interact via REST
curl -X POST http://localhost:8384/api/v1/commit \
  -H "Authorization: Bearer $LIT_TOKEN" \
  -d '{"message":"implement feature X","author":"agent-7","sign":true}'
```

## MCP Server

Lit exposes itself as an MCP (Model Context Protocol) tool server, enabling LLM agents to interact with repositories directly through tool calls:

```bash
lit mcp-serve --stdio    # For stdio-based MCP clients
lit mcp-serve --port 8385  # For HTTP-based MCP clients
```

Tools (30): `lit_status`, `lit_diff`, `lit_log`, `lit_show`, `lit_blame`, `lit_reflog`, `lit_add`, `lit_commit`, `lit_snapshot`, `lit_branch`, `lit_checkout`, `lit_merge`, `lit_resolve`, `lit_rebase`, `lit_cherry_pick`, `lit_revert`, `lit_reset`, `lit_stash`, `lit_tag`, `lit_push`, `lit_pull`, `lit_fetch`, `lit_clone`, `lit_search`, `lit_verify`, `lit_gc`, `lit_init`, `lit_config`, `lit_ontology`, `lit_schema`.

Resources: `lit://status`, `lit://branches`, `lit://log`, `lit://ontology`, `lit://schema`.

## Configuration

```toml
# .lit/config.toml (repo-local) or ~/.litconfig.toml (global)

[core]
default_branch = "main"

[agent]
default_output = "json"       # json | human | msgpack
auto_sign = true              # sign all commits with PQ signatures

[merge]
default_strategy = "recursive"
auto_resolve = true

[security]
encryption = "aes-256-gcm"
fips_mode = true
audit_log = true
```

Configuration priority: CLI flags > environment variables (`LIT_*`) > repo-local > user global > system > defaults.

## Cryptographic Security

- **Hashing**: SHA3-512 + BLAKE3 composite (192 hex chars, quantum-resistant)
- **Signatures**: ML-DSA-87 (Dilithium5) — NIST FIPS 204, Security Level 5
- **Encryption**: AES-256-GCM with PBKDF2-HMAC-SHA512 (600,000 iterations)
- **Audit**: HMAC-SHA256 signed tamper-evident logs
- **Compliance**: FIPS 140-3 Level 1, ISO/IEC 19790:2012
- **Post-quantum**: Resistant to Shor's and Grover's algorithms

See [ENCRYPTION.md](ENCRYPTION.md), [FIPS_140-3_COMPLIANCE.md](FIPS_140-3_COMPLIANCE.md), and [CRYPTOGRAPHY.md](CRYPTOGRAPHY.md) for details.

## Operating Modes

### Standard Mode
Full local and remote distributed VCS over HTTPS, SSH, or `lit://` protocol.

### Airgap Mode
Complete network isolation for classified and air-gapped environments. Physical transports only (USB, file shares).

```bash
lit --airgapped clone file:///media/usb/repo.lit
lit config set airgap.enabled true
```

See [AIRGAP.md](AIRGAP.md) for complete documentation.

## Use Cases

- **AI agent swarms**: Multiple agents collaborating on a codebase via structured API
- **CI/CD pipelines**: Machine-readable output eliminates fragile text parsing
- **MCP-enabled IDEs**: LLM agents operate on repos through tool calls
- **Air-gapped environments**: Government, defense, critical infrastructure
- **Post-quantum security**: Organizations preparing for quantum computing threats
- **Git migration**: Import existing Git repos, export back when needed

## Testing

428 tests across unit, integration, and performance suites:

```powershell
cargo test --lib -- --test-threads=1                   # 61 unit tests
cargo test --test command_tests -- --test-threads=1    # 239 command tests
cargo test --test feature_integration_test              # 30 integration tests
cargo test --test performance_benchmarks --release      # 9 benchmarks
cargo test --test adversarial_test -- --test-threads=1  # 5 security tests
cargo test --test concurrency_test -- --test-threads=1  # 9 concurrency tests
cargo test --test network_integration_test -- --test-threads=1 # 14 network tests
```

## Documentation

- [DESIGN.md](DESIGN.md) — Full agentic-first architecture and design rationale
- [ROADMAP.md](ROADMAP.md) — Implementation roadmap and milestones
- [ARCHITECTURE.md](ARCHITECTURE.md) — System architecture deep-dive
- [TESTING.md](TESTING.md) — Testing guide
- [QUICKSTART.md](QUICKSTART.md) — Getting started guide
- [EXAMPLES.md](EXAMPLES.md) — Usage examples
- [ENCRYPTION.md](ENCRYPTION.md) — Encryption system documentation
- [CRYPTOGRAPHY.md](CRYPTOGRAPHY.md) — Cryptographic design documentation
- [SECURITY.md](SECURITY.md) — Security model and threat mitigation
- [DEPLOYMENT.md](DEPLOYMENT.md) — Deployment and operations guide
- [AIRGAP.md](AIRGAP.md) — Airgap mode documentation
- [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) — High-level project overview

## License

MIT License

## Contributing

This is a security-focused tool. Contributions should:
- Maintain strict network restrictions
- Include security review considerations
- Add appropriate audit logging
- Follow Rust security best practices
- **Include comprehensive tests** (see testing documentation above)
