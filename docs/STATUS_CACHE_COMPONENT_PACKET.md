# `_compute_live_status_cache` — component decision packet

**Status:** submitted for an OPERATOR SCOPE decision. Measured at `3bc1c8a5`. **No extraction performed.**

The reviewer's ruling that produced this: *"Getting `control_plane.py` under 1000 requires a separate
decision packet for the parked `_compute_live_status_cache`/status-cache component and its closure. Do not
start that component as an ordinary slice."* The contents below are the list they specified.

**The one-line finding:** this component is 634 lines of the carrier's 3,580, so moving it does NOT clear
`control_plane.py` either. Clearing the file needs this component AND most of what remains. That reframes
the decision from "may I move the status cache" to "is clearing this file worth the only kind of change
that can do it" — and the second question is the operator's, not mine.

---

## 1. The component

`_compute_live_status_cache`'s transitive closure is **6 functions / 634 lines**:

| lines | function | note |
|---|---|---|
| 551 | `_compute_live_status_cache` | the derivation itself |
| 40 | `_agent_awaiting_input` | no reader outside the carrier |
| 22 | `_terminal_prompt_hint_from_raw` | also read by `api_core/terminal_text.py` |
| 10 | `_status_refresh_after` | also read by `api_core/registration_gates.py` |
| 7 | `_iso_add_seconds` | no reader outside the carrier |
| 4 | `_row_status_note` | read by 7 router modules |

**It is not an SCC.** Nothing in the closure calls back into `_compute_live_status_cache`; it is a tree, and
`_iso_add_seconds` / `_agent_awaiting_input` are leaves. The 551-line function is one long derivation, not a
knot of mutual recursion. That matters: the obstacle is SIZE and READERS, not cyclicity.

**Four carrier functions outside the component call into it** — `_agent_record_to_dict` (`_row_status_note`),
`_gather_status_inputs` (`_agent_awaiting_input`), `_refresh_agent_live_state` and
`_run_contract_reminders_once` (both the derivation).

---

## 2. Readers, direct and indirect

`_compute_live_status_cache` is reached from **12 modules** outside the carrier: four api_core leaves
(`channel_delivery`, `liveness`, `managed_env`, `registration_gates`), `reconcilers/sessions`, and seven
modules in the agents router package. `_row_status_note` is read by seven router modules on its own.

So this is not a private helper that happens to be long. It is the service's status derivation, reached from
nearly every domain — which is exactly why moving it is a bigger decision than its line count suggests.

---

## 3. Transaction and write boundaries

**No function in the closure opens a connection, commits, or rolls back.** Grepped for `get_db(`, `.commit(`
and `.rollback(` across all six: zero hits. `db` is passed in and the caller owns the transaction.

That is the property that makes the DB side of a move straightforward, and it is already the api_core
leaf rule. It is stated here because it is the one thing about this component that is *easy*, and a packet
that lists only obstacles misrepresents the work.

---

## 4. The cache itself — and the invariant that constrains everything

`_LIVE_STATE_CACHE` is **not in this component**. It is a process-global dict owned by
`service/reconcilers/status_cache.py` since v0.5, and the carrier reaches it as
`status_cache._LIVE_STATE_CACHE` — never `from ... import _LIVE_STATE_CACHE`, because that would bind to
whatever object existed at import time and produce two dicts with reads and writes landing in different
ones, silently. `test_process_global_identity` enforces the access form and has already caught that exact
bug once.

**SINGLE-WORKER INVARIANT.** The cache is correct only with one uvicorn process and one event loop. Any
proposal here that changes how the cache is reached must not weaken that, and must not make it *look*
weaker either — the invariant is currently legible because there is one owner module and one access form.

**Timing semantics.** `_status_refresh_after` computes the entry's expiry from heartbeat freshness, not from
worker presence — which is the documented root of two historical false-`online` bugs, both fixed at the read
boundary by `_enforce_live_worker_gate` and `_enforce_env_reachable_gate` rather than in the cache. Any split
must keep the derivation, its expiry rule and those two boundary corrections reasoning about the same thing.

---

## 5. Observable contract

The component's output is the agent-status payload the dashboard and every `comms_agent_info` call read:
`status`, `statusRaw`, `statusNote`, and the derived fields the six-state engine produces. Its contract is
already pinned by the status matrix invariants (`derive()` is the sole authority; the matrix tests gate the
harness×mode cells). **Those tests are the behavioural net that any restructure here would lean on, and they
exist** — which is the strongest argument that this work is *possible*. It is not an argument that it is in
scope.

---

## 6. What a split would look like

Not a proposal to execute — a description so the cost is legible.

- **Move the closure whole** to `service/status_derivation.py`. Mechanical, byte-identical, provable by the
  existing AST+byte standard. Carrier 3,580 → ~2,950. **Does not clear the file**, and produces a new
  551-line function in a new home: one oversized file traded for a smaller oversized file.
- **Split the 551-line derivation by branch** — managed / resident / terminal-backed / offline — into a
  module per mode behind one dispatcher. This is the only shape that gets any file under 400. It is an
  extract-method sequence, so the gate applies and the proof standard is the inline-back round trip already
  used five times this release. It is also the largest single behaviour-risk in the series, because the
  branches share locals computed at the top of the function; the gate's live-out check would refuse most
  naive cuts, which is protective but means many cuts will simply be refused.
- **Do nothing.** `control_plane.py` stays ~3,580 and the goal is met for 11 of 12 files.

---

## 7. Proof plan, if it proceeds

Unchanged from the five extract-method slices already accepted: one tracked pristine fixture per source
function captured with an explicit utf-8 decode; inline-back round trip in the suite, not just at refactor
time; `tokenize`-protected string interiors; measured block indentation; undefined-name sweep before the
suites; stale-owner census for patch targets; `create_app` 124 routes; exactly-one-owner and
no-upward-import assertions. Plus, specific to this component: the status matrix invariants must be run and
named in the receipt, because they are the only thing that would catch a derivation change that the AST
proof cannot see.

---

## 8. The decision

**For the operator, because the reviewer ruled it is not theirs to make:**

1. Is `control_plane.py` in scope for the branch-split restructure (§6, option 2) — the only option that can
   clear it — given that v0.5.x has been "structural only" and this is the largest behaviour-risk in the
   series?
2. Or is the whole-move (option 1) worth doing on its own merits — the carrier drops ~630 lines and the
   status derivation gets a named home — while accepting that no file involved goes under 1000?
3. Or does the series stop with 11 of 12 files cleared and `control_plane.py` documented as the exception?

My recommendation is **option 3 for v0.5.x, then option 2 as its own tagged piece of work** with the
characterization plan first. The reason is not caution about the code: it is that this component is reached
from twelve modules and is the thing the status engine's correctness rests on, and the series' remaining
value does not depend on it. Options 1 and 2 are both defensible; what would not be defensible is starting
option 2 inside a slice labelled structural.
