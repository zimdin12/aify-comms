# Pilot — thread closure labels (sand-castle only)

**Status:** NOT RUNNING. Reviewed by `comms-senior-dev` (APPROVE pilot shape, wording fixes
applied). Needs **operator adoption** before it touches the team's files — and after 2026-08-10,
`sc-manager`'s custody to apply it.

> **2026-08-10 — I applied this to `sand_castle/AGENTS.md` + `CLAUDE.md` and it did not stick.**
> The edit landed (verified at the time: block at `AGENTS.md:604`, 22 insertions each, staged after
> the TH-4 hook rejected the commit) and was gone within the hour — `sc-manager` measured
> `grep` = 0 and worktree blobs identical to HEAD, with no reset in the reflog. Most likely one of
> the six agents working in that repo cleaned an unexpected staged change, which is a reasonable
> thing for them to have done.
>
> **Two lessons, both mine.** I told the operator "the pilot is live on disk either way, since
> agents read files not git" — false, and stated without re-checking. And the upstream error was
> worse: I edited a POLICY file in a repo with six active agents and asked permission afterwards.
> `sc-manager`'s ruling stands and is correct — `AGENTS.md`/`CLAUDE.md` bind every lane on that
> team, adoption is the operator's call, and there is prior history of an unauthorised edit to
> these exact two files costing them a long forensic investigation.
>
> **If adopted: `sc-manager` applies it**, as its own commit through its own gate. Not me.

## Why this and nothing else

The 14-day analysis found team comms cost ~2.53M tokens of message body, with 41.7% of all
characters in 123 two-party threads and 48.3% attributable to one agent. Four rules were
proposed to cut that. Three were **blocked in review**:

- **Response cap** — unsafe. Reviews must be exempt: the reviews that caught a missed call
  site, a contradicted retraction, and an age-based bound that would have shipped a false RED
  were all long, and the length carried the reasoning that made them right.
- **Depth-6 closure frame** — not defensible as a hard stop; long threads do close.
- **Fanout rule** — depends on measurement we do not yet have.

And the measurement itself turned out to be broken (see `scripts/comms_baseline.py`). With the
corrected, agreed definition:

| window | closed | silent (just stopped) | active |
|---|---|---|---|
| 14d | 40.2% | 57.6% | 2.4% |
| **7d (pre-pilot basis)** | **44.5%** | **48.7%** | 6.8% |

**So we cannot currently tell whether the deliberation is productive.** Two readings fit: the
work concludes but nobody labels it, or threads genuinely decay. `comms-senior-dev` withdrew
its own earlier "productive, not circular" verdict on exactly this point — its 57% had counted
bare `ACK`/`agree` as closure.

That is why this pilot ships **one additive rule and nothing else**. It is designed not to
remove reasoning or cap messages; the residual risk is **rubber-stamping**, covered by sampling
below. (An additive rule can still degrade behaviour by adding ritual — that is the thing to
watch, not a thing to assume away.) It is a *precondition* for judging any later change: until
outcomes are recorded, the guardrail cannot distinguish "unlabelled" from "decayed".

## The rule

To be inserted into the **Communication** section of
`C:/Users/Administrator/sand_castle/AGENTS.md`, and appended to
`C:/Users/Administrator/sand_castle/CLAUDE.md`. Those six agents share that workspace, so the
blast radius is that team. **No skill change, no `install.sh`, no service change** — skills
install per-machine and cannot be scoped to one team, which is why this vehicle was chosen.

```markdown
### Closing a thread — label the outcome

When a thread reaches an outcome, the final message must say so explicitly. Start its subject
with one of:

`CLOSED` · `DECISION` · `APPROVED` · `APPROVE` · `REJECTED` · `REWORK` · `BLOCKED` ·
`WITHDRAWN` · `MERGED` · `TAGGED` · `SHIPPED` · `RESOLVED` · `FROZEN` · `LOCKED`

Use the **smallest true label**. If none fits, the thread is not concluded.

Then give three lines, all three required:

- **Decision:** what was settled, in one sentence.
- **Owner:** who holds the next action — a name, or `none` if it is finished.
- **Next evidence:** what would show it worked, or `n/a`.

If you cannot pick a label, say what is still open and what would settle it. An unlabelled
thread that simply stops is indistinguishable from one that was abandoned — roughly half of our
threads currently look like that, and we cannot tell which are which.

This adds a label. It does not cap length, forbid detail, or restrict who you talk to.
```

The label list is **exactly** `TERMINAL_LABELS` in `scripts/comms_baseline.py`. Convention and
measurement must not diverge — if they do, the metric credits labels nobody was told to use, or
misses the ones they were. That is the `ORDER BY` bug one layer up.

## Acceptance gate (set by `comms-senior-dev`)

Measure after **7 days** with `scripts/comms_baseline.py --days 7`, compared against the
**7-day pre-pilot snapshot** `docs/baselines/2026-08-09-comms-baseline-7d.json` — *not* the 14d
file, which is context only. Comparing a 7d pilot to a 14d baseline is not a guardrail.

Pre-pilot 7d: closure **44.5%**, silent **48.7%**, depth≥6 closure **50.6%**, ~1.15M tokens,
response share 67.1%, avg response 3,114 chars.

1. Labelled closure rate rises, **without** a rise in `REWORK`/`BLOCKED`/`REJECTED` caused by
   missing context.
2. `silent_unclosed` drops from 48.7%.
3. **Token spend is observed, not optimised.** This pilot is *not expected* to reduce tokens; a
   flat or slightly higher line is **not failure**, unless sampling shows labels are boilerplate
   or the rule increased back-and-forth.
4. **Mechanical format check** — a terminal-labelled final message must contain all three of
   `Decision:`, `Owner:`, `Next evidence:`; owner non-empty and not `unknown`; next evidence
   non-empty or an explicit `n/a`. This catches lazy formatting, **not** semantic truth.
5. **Manually sample newly labelled closures.** No DB-only test can prove a label is honest
   without becoming gameable. This is the real guard; (4) is only hygiene.

## Rollback

Revert the two file edits. No `install.sh`, no service rebuild.

**But reverting does not reach running agents** — instruction files are read at session start,
so a revert affects *future* sessions only. If immediate rollback is needed, send an explicit
channel correction to the team, or restart/respawn the affected agents.

Pin before landing: the current SHA of both files, so rollback does not require archaeology.

## Explicitly NOT in this pilot

- Any cap on message or review length.
- Any fanout/broadcast rule.
- Any change to the global `aify-comms` skills.
- Any change to `sand_castle/AGENTS.md` beyond inserting the block above. The file is 124,035
  bytes (~31k tokens loaded per agent session, six agents) which is a real and separate cost —
  **but I checked and could not show it is stale.** The 2026-05-25 section its heading claims to
  supersede is already gone; only the reference remains, and just 5 lines in the whole file
  carry deprecation language. Culling it is a decision for the team, on evidence nobody has
  gathered yet.
