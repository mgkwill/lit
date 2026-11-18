# Repository instructions

## MIT baseline and upstream provenance

Build Lit from the MIT-licensed upstream snapshot
`98c59bb2dbebcf7f575993a4ad89e1300a578c83`, its MIT-licensed ancestors,
and our independently authored changes descended from that snapshot. Preserve
upstream MIT attribution. Upstream changed its license at
`7ccae9487a1a573ed870c6501fa191f25e2ab501`; a later upstream release or a
newly checked-out branch is not an implementation baseline.

For implementation, fixes, tests, reviews and delegated work:

- Use the MIT snapshot and our independently authored recovery code as source
  inputs. Verify a proposed source revision's provenance before using it;
  the presence of the MIT baseline in its ancestry alone is insufficient.
- Treat every post-baseline **upstream** version, including v1.8.0, as a black
  box. Derive requirements from executable behavior, CLI help, JSON/schema,
  HTTP/MCP responses, protocol observations and public prose documentation.
  Record the reference version and observations, then write our implementation
  and tests from that behavioral contract.
- Do not copy, adapt, translate, merge, cherry-pick or manually retype post-MIT
  upstream implementation, tests, patches or source-bearing examples. This
  applies to saved refs, Git objects, upstream branches, source archives,
  crate sources, source views in generated documentation and indirect inputs
  supplied by research tools or other agents. Public documentation is not a
  route for importing implementation snippets.
- Use the source and documentation of separately licensed dependencies under
  their own terms; preserve their attribution. Dependency provenance does not
  make post-MIT upstream Lit code an allowed implementation input.
- If an implementation task encounters post-MIT upstream source, record the
  exposure and affected work. Continue independent work from the MIT baseline
  and observed behavior; investigate affected provenance before claiming that
  change is independent. Do not conceal exposure or assume a clean Git graph
  proves absence of manual copying.
- Carry these restrictions into delegated prompts and handoffs. Supply
  behavioral contracts and MIT-derived source, rather than upstream source
  excerpts or source-bearing audit output.

For a requested provenance audit, read
[audits/provenance-2026-11-17.md](audits/provenance-2026-11-17.md). Source
comparisons against post-MIT upstream belong to a separate audit activity;
keep comparison excerpts outside implementation sources and do not use them
as implementation specifications. State the exact audited commit/snapshot,
observed matches, evidence and limitations. The existing audit is bounded to
its recorded revisions and does not automatically cover subsequent work.

## Evidence preservation

Preserve implementation-session records and their source identifiers while
the rewrite continues. After the rewrite is complete, archive reviewed
transcript exports beside the provenance report under `audits/evidence/` and
commit them with a manifest covering session IDs, commit ranges, UTC record
boundaries, original-record hashes, export hashes and any redactions. Keep
unaltered originals outside the repository; remove credentials and private
operational data from committed exports and document the redactions. Label
implementation records separately from provenance-audit records, which may
contain post-MIT comparison source. Treat archived transcripts as evidence,
not implementation inputs. Transcript archival remains pending until it is
actually performed and verified.
