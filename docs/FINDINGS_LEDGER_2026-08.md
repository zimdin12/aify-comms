# External findings ledger — August 2026

Findings reported from the second instance (192.168.100.11) across three rounds: the operational
sweep (#1–#10), the `49067d7→7a9ef0d` review (#11–#12), and the v0.5.4 `7a9ef0d→8528b42c` review
(#13–#14). Every item was re-verified against this checkout before being marked, because two of them
had already been closed by commits the reporter had not pulled, and one was bigger than reported.

**Status vocabulary.** FIXED = code changed here, with a mutation-tested proof. ALREADY CLOSED = was
real when reported, closed before the report arrived. NOT A DEFECT = verified and found sound.
NEEDS RULING = confirmed real, deliberately not patched — the fix changes fleet-wide behaviour and is
an operator/reviewer decision. WON'T FIX (YET) = real but latent, where the fix carries more risk
than the defect. OPEN = should be fixed, not yet done.

| # | Sev | Finding | Status | Where |
|---|-----|---------|--------|-------|
| 1 | MED | Untrusted subject echoed raw; the rule test's regex was anchored to the start of the f-string and could not see it | **FIXED** — and it was **4 sites**, not 1 | `ed3611e5` |
| 2 | MED | extract-method gate false-passes on a caller-shadowed global | **FIXED** — first site was already closed; the **sibling in `conditionally_bound_argument_violations` was live**, confirmed by execution | `0afc6aa0` |
| 3 | LOW-MED | `env_status.py` liveness fails open (falsy status → "online"; `except: pass` on unparseable `last_seen`) | **WON'T FIX (YET)** | — |
| 4 | BUG | Resident session's resume handle never pinned | **OPEN — needs a live rule-out first** | — |
| 5 | GAP | Modal permission prompt freezes a managed session; up-but-deaf, not shown as blocked | **OPEN — mostly not ours** | — |
| 6 | LOW→**MED** | `service.json` overrides the build stamp | **FIXED** — and broader than reported: the same loop reached `build_sha` | `8d7d7c24` |
| 7 | WATCH | Orphaned env-bridge auto-spawn | **OPEN — untraced** | — |
| 8 | LOW | Two doctor gates unrun in the reporter's sandbox | **NOT A DEFECT** — both run green here (9, and 9 + 28 subtests) | — |
| 9 | HIGH VALUE | No way to change a recurring agent's *procedure*, only to message the agent | **OPEN — feature, v0.6** | `docs/V0.6_PLAN.md` |
| 10a | HIGH | Managed/idle agents accept sends but never process them; `available` promises a cold start that yields no reader | **NEEDS RULING** | `status_engine.py` |
| 10b | HIGH | Failure notice is authored **as the dead target**, so the system noticing an agent is dead refreshes its last-produced | **NEEDS RULING** — traced; the obvious fix breaks threading | `dispatch_sweeps.py:65`, `dashboard_run_report.py:211` |
| 11 | MED | `RECONCILER_BORROW_CEILING = 13` against a real 6 — a loosened ratchet | **ALREADY CLOSED** — ceiling is 0 against 0, and the gate now asserts *equality* | — |
| 12 | LOW | Stale counts in the extraction test header | **FIXED** — numbers removed rather than re-pinned | `22bfd2ef` |
| 13 | **HIGH** | Read-receipt injection: a sender's body could mint receipts and silence another agent's messages | **FIXED** — both halves | `81487d0b` |
| 14 | LOW | Allowlist `_comment` still quoted a ruling naming five files, all cleared | **FIXED** | `816b71fb` |

## Open, ranked — what to do next

1. **#10a/#10b — the delivery bug. Needs an operator + comms-senior-dev ruling before any code moves.**
   This is the only item with field evidence of ongoing harm (14 of 17 sampled agents deaf, 59
   messages stranded, read-gaps 5–61 days). It is not one bug:
   - The claim-without-turn-start path is real and the backstop names it
     (`reconcilers/orphaned_managed_runs.py:123`). A successful claim is indistinguishable from
     delivery, and the read receipt is written **at claim** (`dispatch_claim.py:422`), before any
     turn starts — so *last-read is not evidence of health*. Only a content-verified reply is.
   - Re-deriving `available` changes what every deliverability decision in the fleet reads.
   - 10b's `from_agent = target` is **deliberate** (the notice is threaded as a reply to the
     sender's dispatch), so "just change the author" trades a corrupted instrument for broken
     threading. Marking the row instead means repurposing `messages.source`, which carries exactly
     two values today and is a binary discriminator in at least four modules. **Both candidate fixes
     are reader audits, not patches.**
   - Smallest honest first step, if a full ruling is slow: surface the failure where the send
     happens — a send to an agent whose recent claims all timed out at the backstop should say so at
     send time — rather than re-deriving status.

2. **`comms_search` renders result subjects unquoted.** Same class as #1 and **my fix does not cover
   it**: the new AST gate keys on a `Subject:`/`latest:` label, and search renders a bare field with
   no label (`mcp/stdio/search-tool.mjs:81`, `| ${x.subject}`; plus the Python side in
   `service/sse/inbox_tools.py`'s `comms_search`). Fixing the bridge half requires `install.sh` **and**
   a wrapper relaunch, so it is a deploy-coupled change, not a quiet one.

3. **#4 — resident resume handle.** Rule out a stale bridge first (the reporter's own advice). If it
   is genuine, registration should either pin the handle or say *"not capturable — relaunch with
   `--aify-agent`"*, rather than leave a silently un-resumable agent. Same principle as
   *no evidence is not a pass*.

4. **`undefined` is absent from `HANDLE_PLACEHOLDERS`**, so `String(undefined)` can register as a
   session handle. Small, self-contained, bridge-side (deploy-coupled).

5. **#9 — recurring-task definition object.** Feature work for v0.6; the reporter's ranking is right
   (an editable definition is the whole fix; a durable suspend is second). Their closing note is the
   part worth keeping: the *"a message cannot change a procedure"* doctrine in AGENTS.md papered over
   a delivery defect, because the corrections were never read.

## Deliberately not doing

- **#3** — the schema defaults `status` to `'online'` and registration writes both columns, so no
  reachable path was found. If a legacy row does carry an empty `last_seen`, the "fix" flips that
  environment offline, `env-bridge` goes red, and managed spawns look dead. That is a live-fleet
  change for a defect nobody has observed. Fix it the day a corrupt row is actually seen, with that
  row as the test.
- **#7** — no evidence of a spawner. Trace it if one reappears; do not guess.

## What the three rounds taught, beyond the individual bugs

Two of the four gates involved were **green while guarding nothing**: the subject-echo scan was
anchored to the start of an f-string, and the borrow ceiling sat at 13 against a real 6. Both were
detectable only by measuring the gate against reality rather than reading it. The subject scan is now
AST-based with an anti-vacuity check on a planted hostile snippet; the ceiling now asserts equality so
slack fails on its own. **A gate that cannot fail is indistinguishable from a gate that passes.**
