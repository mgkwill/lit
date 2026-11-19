# Standing broker certificate rotation

On November 18, 2026 (Pacific time), the 100latent sandbox replaced all six
standing client leaves and the host-a server leaf. The renewed workers on host-a,
host-e, host-f, and the operator Mac returned the same authorized claim sets
through the overlap and after retirement. The renewed scheduler could read
the same authority state. The renewed observer sent a fresh policy observation
and the broker completed a healthy API v3 controller cycle.

After retirement, each of the six original client certificates independently
received HTTP 403 on the live broker. The primary presented the exact new server
leaf. All managed client profiles use the dedicated server trust CA. The new
trust store rejects the old server leaf. The old leaf key copies were removed.
The existing client identity CA and its private signer stayed in the original
trust boundary, and the broker continues to verify client certificates against it.

The protected main, proposal freeze, and broker namespace rules compared equal
before and after. The native queue was empty and the host-a broker remained on
`cc36a28`. The rotation used independently authored recovery tooling.

[Structured results](broker-standing-rotation-results-2026-11-18.json) record
certificate fingerprints, identities, and the observed denials.
