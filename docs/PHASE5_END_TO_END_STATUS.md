# v0.6 Phase 5 — end-to-end proof: what is proved, and what is waiting on the operator

The gate has two halves. The proof half is complete and green. The deploy half needs three operator
actions, one of which I was explicitly told not to take.

---

## Proved

### The Phase 0 pass set is intact

`service/tests/e2e` — **11 passed, identical to `baseline.json`.** Not one test had to be changed to
keep passing, which is the thing the baseline exists to detect: a test edited until it goes green
again is a behaviour change wearing a refactor's clothes.

### Every suite, at the end of v0.6

| suite | result |
|---|---|
| `service/tests` | **4,176 passed** (+8,290 subtests) |
| `mcp/stdio` | **333 suites** |
| `service/new_dashboard` | **1,135 passed** |
| `aify-wrapper` (new repo) | **27 passed** |

For comparison, CLAUDE.md's snapshot at the start of this release read 3,991 / 318 / 1,097.

### One correction found while re-verifying

`baseline.json` carried `"summary": "8 passed in 16.33s"` beside `"passing": 11` and a list of 11
tests. The prose was written before the last three tests were added and never re-derived. The field
the gate actually reads was right; the sentence beside it was not — which is precisely the class of
defect this file exists to catch, found in the file itself. Corrected, with the history kept.

---

## Not proved — three operator actions, in order

`aify-comms doctor` is 5 red, and **every one is a deploy step rather than a defect.** The checks are
doing exactly their job: reporting that what is running is not what the checkout says.

| check | what it needs | disruptive? |
|---|---|---|
| `service` | `bash scripts/stamp.sh && docker compose up -d --build` — 2 commits changed code the service executes (#10b's outbound guard, the rename repoint) | brief service restart |
| `skills-installed` | re-install skills; 13 files differ | no |
| `bridge-installed`, `wrapper-current` (aify-wrapper's since v0.6) | `install.sh` per client — 12 commits touched `mcp/stdio/`, and the wrappers are all pre-contract builds | no: files are replaced, running processes keep what they loaded |
| `bridge-current` | **relaunch every wrapper** | **yes — this stops and restarts the operator's working agents** |

I have not run any of them. The operator said the team is working and that bridges can no longer be
restarted, and `bridge-current` cannot go green without exactly that. Doing the first three anyway
would leave the fleet in a state where the files on disk are new, the running processes are old, and
the one check that would have said so is still red for a different reason.

**Recommended order when there is a window:** stamp and rebuild the container, re-run `install.sh`
for claude, codex and hermes (sequentially, never in parallel), then relaunch the wrappers, then
`aify-comms doctor --strict`. The wrapper change is the one to watch: Phase 2 rewrote all four
launchers, and although they are covered by 17 executing behaviour tests plus 14 contract tests plus
render guards, a wrapper defect shows up as an agent that will not start.

## Also outstanding

- **The live two-session round-trip**, which no scripted suite replaces. It needs two real runtimes
  and therefore the same window.
- **comms-senior-dev's review of the whole-release diff.** The plan is explicit that the tag comes
  after that approval, and my standing instruction is that shipping past a verdict sha is unreviewed.
  The diff to review is `v0.5.7..HEAD`.

Until those land, v0.6 is code-complete and unreleased, which is the honest description.
