# Unattended broker takeover qualification

On November 18, 2026 (Pacific time), the watcher took over from host-a to host-b
in 5.63 seconds after a SIGKILL of
the source fixture broker. Both processes used native systemd services and
mTLS. The fixture used a loopback provider with the sandbox policy contract.
The live 100latent broker, queue and protected main rule stayed in place.

The watcher authenticated the primary before arming. It detected repeated
connection failures, confirmed the source process was stopped over pinned SSH,
installed and exercised a durable start condition, and obtained a fresh SQLite
checkpoint. The standby preserved the original worker claim and capability
and completed a fresh controller cycle. A later systemd start attempt on the
source was skipped under the persistent fence.

In the negative case the management host was unreachable. The watcher refused
promotion and the standby PID stayed at zero. Regression tests also cover
credential errors, conflicting policy or host identity, unknown restore and
activation responses, and rejecting a checkpoint without a matching journal.
Temporary services, accounts and keys were removed after qualification.

The tested takeover requires readable source authority and positive source
fencing over the management path. A separate recovery design would be needed
for a whole-host loss with inaccessible state. Post-MIT upstream remained a
black box.

[Structured receipts](broker-unattended-failover-results-2026-11-18.json).

## Client and observer continuation

The follow-up drill required an authenticated policy observation. The same
observer feed reached host-b after host-a exited. The CLI recovered the original
claim through its configured standby URL. Verified takeover took
7.94 seconds. All 115 regressions and
the 100-identity provider-failure stress test passed on macOS, host-a, host-e,
and host-f. The tests cover polling a transferred operation without resubmitting
its POST and withholding fallback on ambiguous writes or TLS validation errors.

## Live sandbox validation

With the 100latent queue drained, the watcher automatically promoted a
temporary host-b standby after a planned stop of the canonical host-a service.
The standby completed a fresh controller cycle under the real GitHub App
and policy observer in 10.35 seconds.
The CLI, given the primary and standby URLs, returned the identical worker
claims through the standby. The operator stopped host-b before returning
authority to host-a. Claim, proposal, action and admission records matched
after failback. The protected main, namespace and freeze rules compared
equal throughout the drill. The temporary standby, credentials and fencing
files were removed after the canonical broker resumed.
