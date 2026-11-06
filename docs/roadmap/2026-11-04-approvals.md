# Roadmap approval record

## Current review target

**STACK-2026-11-04-R2 — planning only.** An approval covers the owner's repository
scope and shared goals, dependencies, sequencing and explicit decision gates. It grants
no implementation, activation, reset, spend, public edit, release or restart authority.

| File | SHA-256 |
|---|---|
| `docs/ROADMAP.md` | `4a39b9575cef32f71f932f67258a3bf320239ff54792088b6f95e2588f954ffa` |
| `docs/roadmap/2026-11-04-issue-map.md` | `f9549413202f33a541fea3756cd149ed9ddbd00a5a4352ba717de4fb30eee3ae` |

The map still covers 109 unique current open issues exactly once. HP-1 is a separately
named planned integration gap, not an invented current issue. Both exact R1 files were
retained and verified against their original hashes before freezing R2.

### R2 changes from R1

1. `providerCheckout` remains an operator-granted host execution setting read fail-closed;
   dashboard owns dependency requirements, not authority to silently write that grant.
2. The existing dashboard pre-push gate is described accurately; Linux execution location
   and cost remain a deliberate qualification step, not an already-present gate.
3. HP-1 names the env-owned host-provider adapter and tracking prerequisite before G3.
4. Dashboard rows carry only neutral phase/dependency/abstract exit criteria; private
   source locations, line-level findings and personal commentary remain in private plans.

## Current owner decisions

| Repository owner | R2 decision | Scope / evidence |
|---|---|---|
| aify-comms | APPROVE | Comms scope and shared sequence, with the four corrections and all named operator/producer gates retained. |
| aify-env | APPROVE | Explicit R2 confirmation; both hashes and unchanged 18 env rows verified; producer/receiver, host-grant, singleton and activation gates retained. |
| aify-wrapper | APPROVE | Explicit R2 confirmation; retained R1/current R2 hashes and unchanged 17 wrapper rows verified; custody, consuming-pin and lifecycle gates retained. |
| aify-dashboard | APPROVE | All four R1 corrections confirmed on exact R2 hashes; 20 abstract dashboard rows checked; private source details remain private. |
| aify-project-graph | APPROVE | Explicit R2 confirmation; exact hashes, 18 unchanged APG rows and 109-ID inventory checked; host/use/viewer/import/qualification gates retained. |
| agents-setup | APPROVE | Explicit R2 confirmation; exact hashes, seven unchanged setup rows and shared custody/HP-1/privacy corrections verified; reset/adoption gates retained. |

**All six repository owners explicitly approved the same R2 core and issue-map bytes.**
The five peer confirmations were received; the comms owner records their own planning
approval. This is not independent runtime certification or approval of other owners' code.

All six decisions bind the current named bytes. Earlier slice/R1 approvals did not
silently become R2 approvals. Confirmation is scoped to these corrections and retained
owned seams; it is not a new source audit. Source inspection and inventory checks are not
runtime or implementation qualification.

## Retained first-round decisions

R1 core SHA-256: `55b0459c3b6abd0220c087ae0220554453b221ed9473d46762410f06ab627773`.
R1 issue map SHA-256: `0121caa5f4ca2b1d75f4c5b1a1aece9c014a596ccccf2b08888bdbdee468a7f2`.

| Owner | R1 result |
|---|---|
| aify-comms | APPROVE |
| aify-env | APPROVE |
| aify-wrapper | APPROVE |
| aify-dashboard | REVISE — four specific changes, now incorporated in R2 |
| aify-project-graph | APPROVE |
| agents-setup | APPROVE |

The separate model-pin operation completed for the nine verified open-session targets;
automatic sync stays capability-blocked. This approval record is outside the two review
target hashes so recording a response does not change what it approved. Raw receipts,
source hashes and retained R1 bytes stay in the private planning custody at
`~/projects/comms-evidence-audit-20261104/`.

The retained copy `docs/history/ROADMAP-before-STACK-2026-11-04-R1.md` differs from the
original in one link target only: it moved into `history/`, so its link to the 2026-08 roadmap
now points beside it. The exact original bytes are `docs/ROADMAP.md` at commit `1c9611b3`
(SHA-256 `e41792a6f5b7207931273fcab60d8851e777bf42d39600bbc623fd9b7e2a8436`).
