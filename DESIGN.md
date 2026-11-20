# OpenLit design choices

OpenLit helps people and agents save work, inspect results, and recover from
interrupted operations. The first release favors a reliable shared workflow
over implementing every upstream convenience command.

## Make results readable by programs

Use structured responses with operation results and error codes. Batch input is
one JSON operation per line. MCP tools use the same command contracts. A client
must inspect reported failures rather than assuming that receiving JSON means
success. See [examples](EXAMPLES.md).

## Keep each attempt separate

An attempt starts from a full commit ID and gets its own working directory.
The scheduler owns task assignment and increments the generation when it
reassigns work. Old capabilities cannot publish as the new owner. File overlap
hints help planning but do not establish ownership or lock every editor.

## Put shared writes behind checks

Workers should not hold GitHub write tokens. The broker publishes proposals;
the forge tests their actual integration candidate. Actions-only means a native
GitHub Actions check, not a manually posted success. A protected queue prevents
an agent from declaring its own change integrated.

## Preserve uncertain outcomes

If a remote write may have succeeded before a reply was lost, keep that unknown
outcome in the journal. Reconcile it against external evidence before retrying.
Do not turn a timeout into either an invented success or a duplicate write.

## Reuse Git where it helps

Git provides remotes, history tools, and forge interoperability. OpenLit supplies
machine-readable operations and recovery contracts. Full replacement of every
Git workflow is not a 0.1.0 promise.

## Keep claims tied to evidence

A local test, a hosted check, a lab test, a signed archive, and a live merge prove
different things. Reports name the commit, platform, and limits. Older upstream
versions may be observed as executables; post-MIT source is not an implementation
input. See [AGENTS.md](AGENTS.md) and [testing](TESTING.md).
