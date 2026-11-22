# OpenLit security

OpenLit 0.1.1 is an early release. Its tests check specific behaviors; they do
not establish that every command or deployment is safe for hostile input.
OpenLit is not a FIPS-validated cryptographic module.

## Separate workers and authority

Give each worker its own working directory and OS permissions. A pinned
workspace separates files and records a base commit; it does not confine a
process. Agents sharing an OS account may read each other's credentials.

The hosted broker keeps GitHub write credentials away from workers. Workers
use individual TLS identities and submit proposals. The scheduler owns task
assignments. Reassignment revokes the previous generation and retires its
proposal before granting the successor permission to publish.

The Actions-only rule requires GitHub Actions checks on the actual merge
candidate, a pull request, and the protected merge queue, with no bypass actors.
It does not replace authentication. GitHub administrators and privileged host
operators remain trusted. See [broker deployment](tools/deployment/README.md).

## Sandbox limits

Use strict sandbox mode when you require enforced filesystem and network
restrictions. On supported macOS and Linux systems it prepares isolation before
starting the command. If that fails, it returns `SANDBOX_UNAVAILABLE` without
launching the payload. Linux requires the host's namespace and confinement
facilities. Windows currently has no strict backend.

Default sandbox mode clears parts of the environment but does not enforce
filesystem and network isolation. Read `controls_enforced` and
`controls_unenforced` in the result. Airgap configuration controls OpenLit's
transport choices; it is not a host firewall. See [offline work](AIRGAP.md).

## Protect data and credentials

Encryption protects stored object payloads and the encrypted index. It does not
hide working files, every reference, object IDs, or pack sizes. Keep keys,
passphrase files, usage ledgers, and backups under appropriate OS permissions.
Use separate encryption keys on hosts that cannot share usage accounting.
See [encryption](ENCRYPTION.md) and [key handling](KEY_DISTRIBUTION.md).

Use Git credential helpers or SSH agents for Git remotes. Do not embed passwords
or tokens in URLs. Keep SSH host checks and HTTPS certificate checks enabled.
Local HTTP/MCP endpoints are not a general multi-user authorization service.

## Report a problem

Contact the repository owner privately for a suspected vulnerability. Do not
include credentials, private source, or a working exploit against a live service
in a public issue. Include the exact commit or binary hash, platform, affected
operation, and a minimal disposable reproduction. This repository does not yet
advertise a dedicated private disclosure endpoint or response-time guarantee.

Release signatures establish publisher identity; they do not certify the program
is bug-free. See [release signing requirements](docs/RELEASING.md).
