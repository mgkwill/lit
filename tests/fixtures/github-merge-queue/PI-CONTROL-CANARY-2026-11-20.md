# Pi checkpoint takeover and launch limits

The `pinned-pi-930583f7` canary used recovery commit `e72ef2d` and the
100latent sandbox at Git pin `8e87977e5a4a23d56a5fcb99ffd5d507632ea33e`. Six Pi agents ran across
host-a, host-e, and host-f. All inference used host-a.

## Simulated controller loss

The checked-in controller saved the reassignment intent and sent a
checksum-verified checkpoint to host-a before submitting the assignment.
The checkpoint's journal held the intent and had no assignment receipt.
The outgoing disposable controller process exited after the broker
accepted that submission.

The checked-in takeover command restored the off-host checkpoint and
changed the two temporary scheduler identities. The old credential
received HTTP 403. The standby adopted the same claim from the broker
after a total of one reassignment submission. The Mac and lab hosts
remained powered on, and the standing policy observers stayed available.

The planned handoff withdrew [#669](https://github.com/100latent/lit-merge-queue-sandbox/pull/669) and published
[#670](https://github.com/100latent/lit-merge-queue-sandbox/pull/670). Git independently reproduced the source
conflict on [#667](https://github.com/100latent/lit-merge-queue-sandbox/pull/667). The replacement [#671](https://github.com/100latent/lit-merge-queue-sandbox/pull/671)
preserved the peer's function and passed both sets of source checks.

## Bounds and protected integration

The live canary used two concurrent SSH commands per configured host
and two admitted inference sessions. A separate mocked 100-operation
stress test reached and stayed within four SSH commands per host and
eight admitted inference sessions.

The final tree at `61a19a9b3a5edbf335443a19362447660d7fa697` passed 1,014 source assertions and
`git fsck --full --strict`. The audit verified six exact Actions-only
merges, eight successful PR checks, and two retired PRs. Both stale
attempts were denied publication and registration. The audit found no
bypass actors or manual commit statuses.

Cleanup restored the original rules and broker configs, denied all
six temporary worker identities, and removed the controller's temporary
keys and SSH access. Replaying the completed standby run returned the
same cleanup receipt after the keys were removed. The native queue
emptied. All 170 regressions passed
on macOS, Linux x86-64 on host-a, and Linux arm64 on host-e and host-f.
The follow-up at `580c808` makes four SSH commands per host
and eight inference admissions the defaults, and refuses an ambiguous
controller roster. The same 170 regressions passed on all four targets.
Post-MIT upstream stayed black-box.

[Structured evidence](pi-control-canary-2026-11-20.json)
