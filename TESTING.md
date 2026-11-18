# lit Testing Guide

## Automated Test Suite

Lit has a comprehensive automated test suite with **428 tests** covering all features:

- **239 CLI command tests** (coverage across 44 command modules incl. HTTPS, SSH, and lit:// transport)
- **61 unit tests** (core objects, crypto, network, storage)
- **Integration tests** (core functionality, network transport)
- **Performance benchmarks** (baseline metrics)
- **Adversarial/security tests** (airgap validation)

### Quick Start

```powershell
# Run all command tests (REQUIRES --test-threads=1)
cargo test --test command_tests -- --test-threads=1

# Run integration tests
cargo test --test feature_integration_test

# Run performance benchmarks
cargo test --test performance_benchmarks --release -- --nocapture
```

### Concurrent agent integration

Run the Git-backed workflow with Python 3 and Git on PATH. Pass the absolute
path to a recovery build of Lit.

```sh
python3 tests/agent_workflow_regression.py /absolute/path/to/lit
```

The default runs provision separate worktrees at a pinned base before
releasing 10, 50, and 100 workers to edit, commit, and submit concurrently.
The fixture checks both disjoint edits and a shared-file conflict.
It round-trips the exact integration candidate through Lit, then uses a Git ref
transaction to verify the verdict, current main, and current task attempts
before recording publication receipts. It exercises missing verdicts,
reassigned tasks, stale main, and duplicate publication.

The workflow uses disposable local repositories and trusted fixture processes.
A hosted deployment needs a forge or service to authenticate writers and
restrict writes to claims, verdicts, receipts, and the protected main branch.

### GitHub merge queue

The `Agent workflow candidate` check runs a 10-worker preflight for pull
requests and the 10-, 50-, and 100-worker suites for GitHub's
`merge_group.checks_requested` event. Both paths verify the checked-out commit
against the event before building Lit. The Actions summary records the event
and candidate SHA.

In a disposable queue-enabled organization repository, require a pull request,
the merge queue, and the `Agent workflow candidate` check from GitHub Actions
on the target branch. Record the effective branch rules and active queue
before the test. Use a separate worker credential with access to its assigned
branch namespace. Exercise these cases with real GitHub writes:

- Push a new commit to the assigned branch and confirm its remote tip.
- Attempt a new commit on another worker's branch and a direct push to the
  protected base. Capture both server rejections and confirm both tips.
- Queue an allowed pull request and capture the merge group and check run
  SHAs. A passing check on the pull request's earlier SHA must leave the
  queued group waiting. Exercise a failing merge-group check and confirm
  the protected base remains at its earlier tip.
- Queue a passing candidate. Capture its check result, the merge queue result,
  and the final base tip. Compare the applied changes if the configured merge
  method changes commit IDs.

The [GitHub queue fixtures](tests/fixtures/github-merge-queue) define a
disposable test with `main`, `agents/worker-a`, and `agents/worker-b` seeded
from the same commit. Install `branch-permissions-probe.yml` under
`.github/workflows/` in the sandbox only. Apply `worker-scope.json` and
`protected-main.json` as repository rulesets after seeding those branches.
The probe uses its job's short-lived Actions token for the three real pushes
and checks the resulting remote tips.

For the sandbox's controlled failure, apply `sandbox-gate.patch` with
`git apply --unidiff-zero` before seeding. Dispatch the probe with `fail` to
create the first candidate and `pass` to replace it after GitHub records
the failure. An operator opens and enqueues the pull request.

If Actions does not start an automatic run, the patch allows a manual
dispatch on the exact PR head or GitHub-created `gh-readonly-queue` ref.
For this sandbox fallback, replace the required Actions check in the main
ruleset with a separate commit-status context, such as
`sandbox/agent-workflow-attested`. Leave the queue and other branch rules
enforced with no bypass actors. Once a hosted run completes, verify its
SHA and conclusion, then report that result to the commit-status context
on the exact SHA and link to the run.

Record the event, checked SHA, check conclusion, and protected tip. A check
on the PR head alone cannot qualify queue enforcement. The manual fallback
qualifies GitHub's queue with an external status reporter and leaves
automatic Actions event delivery to its own test.

#### Actions-only qualification

Keep `protected-main.json` active with an empty bypass list for both the
failing and passing trials. Record the ruleset before and after each trial.
Open a fresh sandbox PR and wait for the automatic `pull_request` run of
`Agent workflow candidate` to pass before enqueueing. Record both the PR head
and the checked-out PR test merge SHA. GitHub can attach the check run to the
PR head even when the job checks out the test merge.

For each queue attempt, capture GitHub's queue entry, its candidate SHA, and
the automatic `merge_group` run. Check that the required check came from the
GitHub Actions app and that the PR and queue commits have no manually posted
commit statuses. The failing candidate should leave the queue with `main`
unchanged. For the passing candidate, compare the tested SHA, the PR's merge
commit, and the protected branch tip. The fixture's merge-commit mode expects
all three to match.

#### Multi-host queue qualification

Use repository-scoped, one-job runners on the lab hosts. Give each worker a
unique runner label and a separate workflow job so GitHub issues a distinct
short-lived token. Pin every proposal to the same base and assign a separate
branch and file to each worker. Record the runner, host, and pushed SHA.
Compare the protected and out-of-scope refs before and after denied pushes.

Open one PR per worker push as the operator so the automatic
`pull_request` checks can run. Enqueue the checked heads together, record
queue depth and simultaneous builds, and verify the Actions check against
each PR's merge commit in the protected branch's first-parent history.
Use the PR timeline's merge event to cross-check Git when the PR summary
reports a different SHA. Keep the Actions requirement and bypass policy
active when tuning queue capacity. Restore any sandbox settings
changed for the trial and remove the temporary runner directories.

Per-job tokens exercise separate credentials for a repository's Actions
identity. Per-agent GitHub identities and ownership enforcement between
allowed worker branches need their own qualification.

#### GitHub App branch ownership

Install a distinct private GitHub App per worker on the sandbox, with
Contents and Pull Requests write access. Give each branch its own active
ruleset for creation, updates, and deletion. Give only the assigned App
bypass access to that branch's ruleset. Keep those bypasses separate from
protected `main` and keep every other worker ref covered by the catch-all
ruleset. Add the per-branch rules before excluding their refs from the
catch-all.

Run [the ownership worker](tests/fixtures/github-merge-queue/app-ownership-worker.py)
on each assigned host with a sandbox-scoped installation token on stdin.
Run `publish HOST APP_SLUG BASE_SHA` for all three workers before running
`challenge HOST APP_SLUG BASE_SHA` on each. Check that the App created the
PR and that its head equals the published commit. The challenges construct
real fast-forward updates and require GitHub to reject them for both peer
branches and `main`. Compare each ref before and after the attempted update.
Verify the automatic `pull_request` and `merge_group` checks using the
Actions-only procedure above.

#### Multi-App PR burst

Run [the burst worker](tests/fixtures/github-merge-queue/app-burst-worker.py)
on `host-a`, `host-e`, and `host-f`, each with its own App installation token.
Use `HOST APP_SLUG BASE_SHA RUN_ID`. The worker checks its host and token
repository scope, then creates ten separate worktrees pinned to the base.
Nine proposals add unique files. The tenth adds `shared.txt` with different
content from each host, producing an intentional add/add conflict.

Authorize exactly ten temporary branch refs per App under
`agents/app-burst/RUN_ID/HOST/00` through `09`. Add those refs to the assigned
App's ownership rule before excluding them from the catch-all rule. Keep
the required Actions integration and `main` bypass policy unchanged.

Keep each SSH worker's stdin open. Send its installation token as the first
line. After all three workers emit `ready`, send the same near-future Unix
start time to each. Each runs three concurrent publishers. Collect all 30
`published` receipts, then send `challenge` to all workers after every host
emits `published_all`. Require all nine prohibited fast-forward pushes to
fail without changing their target refs. Successful workers revoke their
installation tokens and remove their temporary worktrees.

Wait for all 30 automatic PR checks before enqueueing their exact heads.
Enqueue the 27 disjoint proposals before the three shared-file proposals.
Record any temporary queue capacity change and restore it after the run.
Expect 28 protected merges and two open conflicting proposals. Verify every
successful queue candidate's checked-out SHA, Actions check SHA, timeline
merge event, and Git first-parent commit. Check all final file contents,
then use `git merge-tree --write-tree` to confirm the two held conflicts.
Check that their original heads and contents remain intact. Restore the
catch-all before removing temporary ownership exemptions, remove host
source directories and local signing files, and preserve adverse receipts.

Check that losing proposals actually leave the queue. Preserve any stale
`UNMERGEABLE` entry and unknown PR mergeability as an adverse result before
withdrawing it. Verify the conflicting head with Git and compare proposal
and main refs around cleanup. Operator withdrawal does not qualify fully
automatic queue cleanup. See the [2026-11-17 burst results](tests/fixtures/github-merge-queue/BURST-2026-11-17.md)
for the observed stale-entry case and exact tested commits.

#### Durable queue coordinator

Use [the coordinator guide](tools/README.md) to enroll immutable proposal
heads, retain a durable journal, and run queue admission and conflict cleanup.
The integration suite runs through the public CLI against a stateful HTTP
forge and real Git, including process death, lost replies, competing writers,
revoked credentials and delayed merge visibility:

```sh
python3 tests/github_queue_coordinator_regression.py
```

For hosted qualification, lock proposal updates with a separate exact-ref,
no-bypass ruleset before admission. Observe policy with an operator credential
when publisher Apps cannot see bypass metadata. Keep the writer token scoped
to the sandbox. Test restart and writer revocation against the same state
directory, and compare the number of durable admission intents before and
after recovery. Queue entry absence alone is not evidence of removal or
failure; wait for a definitive PR outcome. Do not count fail-closed unknown
enqueue outcomes as a completed unattended workflow.

### Documentation

- 📋 **[TESTING_QUICK_REFERENCE.md](TESTING_QUICK_REFERENCE.md)** - Command cheat sheet
- 📊 **[TEST_SUITE_SUMMARY.md](TEST_SUITE_SUMMARY.md)** - Comprehensive test summary
- 📈 **[TEST_COVERAGE_PLAN.md](TEST_COVERAGE_PLAN.md)** - Coverage tracking
- 📁 **[tests/commands/README.md](tests/commands/README.md)** - Command test details

---

## Manual Testing

### Basic Workflow Test

1. **Initialize a repository:**
   ```bash
   mkdir test-repo
   cd test-repo
   lit init
   ```

   Expected output: `Initialized empty lit repository in .lit`

2. **Create and add files:**
   ```bash
   echo "Hello, Lit!" > README.txt
   echo "print('test')" > test.py
   lit add README.txt test.py
   ```

   Expected output: `Added 2 file(s) to staging area`

3. **Check status:**
   ```bash
   lit status
   ```

   Expected output: Shows files in "Changes to be committed"

4. **Commit changes:**
   ```bash
   lit commit -m "Initial commit"
   ```

   Expected output: `[main xxxxxxxx] Initial commit`

5. **View history:**
   ```bash
   lit log
   ```

   Expected output: Shows commit with message "Initial commit"

6. **Create a branch:**
   ```bash
   lit branch feature-test
   lit branch
   ```

   Expected output: Lists both `main` (with *) and `feature-test`

7. **Switch branches:**
   ```bash
   lit checkout feature-test
   lit branch
   ```

   Expected output: Now `feature-test` is marked with *

8. **Make changes on branch:**
   ```bash
   echo "Feature work" > feature.txt
   lit add feature.txt
   lit commit -m "Add feature"
   ```

9. **Switch back to main:**
   ```bash
   lit checkout main
   ls
   ```

   Expected: `feature.txt` should not be present

10. **View log on different branches:**
    ```bash
    lit log
    lit checkout feature-test
    lit log
    ```

### Network Security Test

1. **View configuration:**
   ```bash
   lit config show
   ```

   Expected: Shows network and security configuration

2. **Try to add invalid remote:**
   ```bash
   lit remote add bad http://github.com/repo.git
   ```

   Expected: Error - only `lit://` protocol allowed

3. **Add valid intranet remote:**
   ```bash
   lit remote add origin lit://192.168.1.100/repo.lit
   lit remote -v
   ```

   Expected: Shows the configured remote

4. **Check audit log (if enabled):**
   ```bash
   cat ~/.lit/audit.log
   ```

   Expected: Shows logged network operations

### Object Inspection Test

1. **Show commit:**
   ```bash
   lit log --oneline
   # Copy a commit hash
   lit show <hash>
   ```

   Expected: Shows commit details

## Automated Testing

Run the test suite:

```bash
cargo test
```

### Test Coverage

The current test suite covers:
- Object hash generation (SHA3-512 + BLAKE3)
- Blob, Tree, Commit, and Tag creation
- Index operations
- Object storage and retrieval
- CIDR network matching
- URL parsing
- Transport detection (HTTPS/SSH/lit://)
- Diff engine with word-diff and stat modes
- Encryption and passphrase caching
- Airgap validation and audit logging

## Integration Testing

### Test with Real Repository

```bash
# Create a test project
mkdir my-test-project
cd my-test-project
lit init

# Create multiple files
echo "# Project" > README.md
echo "TODO items" > TODO.txt
mkdir src
echo "fn main() {}" > src/main.rs

# Add and commit
lit add .
lit commit -m "Initial project structure"

# Create branches
lit checkout -b dev
echo "Development work" > dev.txt
lit add dev.txt
lit commit -m "Dev work"

# Switch back and verify isolation
lit checkout main
# dev.txt should not exist here

# View history
lit log --count 20
```

## Performance Testing

### Large Repository Test

```bash
# Create many files
for i in {1..100}; do
    echo "File $i" > "file_$i.txt"
done

# Add all
lit add .

# Commit
lit commit -m "100 files"

# Check status
lit status
```

## Error Handling Tests

1. **Not in a repository:**
   ```bash
   cd /tmp
   lit status
   ```
   Expected: Error - "Not in a lit repository"

2. **Empty commit:**
   ```bash
   lit commit -m "Empty"
   ```
   Expected: Error - "Nothing to commit"

3. **Delete current branch:**
   ```bash
   lit branch -d main
   ```
   Expected: Error - "Cannot delete the currently checked out branch"

## Cleanup

```bash
# Remove test repositories
rm -rf test-repo my-test-project
```

## Known Limitations

The following features are not yet implemented:
- [ ] Network operations (push, pull, clone) - requires server
- [ ] Merge functionality
- [ ] Interactive staging
- [ ] Diff viewing
- [ ] Deep directory nesting in trees
- [ ] Binary file detection
- [ ] Large file handling
- [ ] Submodules
- [ ] Tags
- [ ] Stash

## Reporting Issues

When reporting issues, include:
1. lit version: `lit --version`
2. Steps to reproduce
3. Expected behavior
4. Actual behavior
5. Operating system
6. Error messages
