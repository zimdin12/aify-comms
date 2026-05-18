# Dashboard Architecture Plan (proposal — operator approval required)

Status: **DRAFT for operator decision.** No code/rewrite on this branch; the
build happens on a dedicated next branch after approval. Co-authored:
`comms-tech-lead` (service/dashboard) + `comms-senior-dev-pi` (bridge/runtime/MCP
— see §6, its independent critique).

## 1. Problem (evidence, not vibes)

`service/dashboard.html` is a single ~7k-line file with all JavaScript and CSS
inline, served verbatim (COPY'd into the container image — no build step). There
are no UI tests and no componentization. Consequences observed this branch:

- Every edit is global and untyped; small changes silently regress others.
- No automated UI verification → regressions surface only via operator testing.
- The status-display logic, console rendering, chat, spawn, settings all share
  one scope — fixes in one area destabilize another.

This is a genuine architecture problem. It is **not** a reason to discard the
HTTP API or MCP layer, which are stable, tested (142 python + 28 stdio files),
and the contract everything depends on.

## 2. Options

**A. Big-bang React rewrite (rebuild dashboard from scratch).**
- Pros: clean component model, types, ecosystem, bulk-actions/UX done "properly".
- Cons: highest risk for a serious in-production control plane; freezes feature
  work for weeks; reintroduces just-fixed behavior (status, console, decouple,
  retention); a long window where the new UI lags the old in coverage. Strongly
  not recommended as the first move.

**B. Staged modernization (recommended).** Same API/MCP, incremental:
1. **Add a build step + extract** inline JS/CSS into ES modules/components with
   **zero behavior change** (mechanical, diff-verifiable, regression-tested).
2. **Add UI smoke/contract tests** (Playwright or similar) covering the flows we
   keep breaking: status display, console decouple, chat send, spawn, settings.
   This is the single biggest lever against "changes introduce issues".
3. **Introduce a component layer incrementally**, page-by-page, behind the
   unchanged API. Each page migrates + ships independently with its tests.
4. Build bulk-actions and the missing UX **on the componentized base**, not the
   monolith.

**C. Do nothing / keep patching.** Rejected — the friction is real and compounding.

## 3. Recommendation

**Option B.** Stage it behind frozen contracts. Rationale: keeps the product
working and shippable throughout, makes each step verifiable, and front-loads
the highest-value, lowest-risk win (build split + UI tests) before any framework
adoption. A rewrite, if ever justified, becomes low-risk *after* step 2 because
behavior is then test-pinned.

## 4. Framework decision (operator input wanted)

The container serves one static dashboard file today. Adding a build toolchain
is the real cost, independent of framework choice. Candidates:

- **React** — largest ecosystem/familiarity; heavier; needs bundler.
- **Preact / Svelte / lit** — far lighter, smaller bundle, simpler build; better
  fit for a single-served-file model; less ubiquitous.

Recommendation: pick the lightest option the team is comfortable maintaining;
the win is componentization + tests + a build, not React specifically. **Decision
point for operator: React vs a lighter framework.** (Defer until step 1/2 — the
extraction is framework-agnostic.)

## 5. Sequencing without regression thrash

Apply TEAMWORK_STRATEGY.md: each migration step gets a validation contract
(behavioral assertions) written first; an independent agent verifies behavior
end-to-end; deploy behind the AST+tests+clean-tree gate; serial execution on
shared files. Migrate lowest-risk pages first; keep the old path until the new
page passes its contract. API/MCP frozen the entire time.

Decision points the operator owns: (a) approve Option B; (b) framework choice;
(c) acceptable per-step verification bar; (d) whether bulk-actions wait for the
componentized base (recommended) or get a stopgap on the monolith.

## 6. Bridge / runtime / MCP (owned by comms-senior-dev-pi)

> _Pending comms-senior-dev-pi's independent critique + this section. Working
> position: FREEZE MCP and the HTTP API — they are the stable, tested contract;
> rebuilding them is high-risk with no demonstrated need. Bridge/runtime
> (`mcp/stdio`) is already modular and test-covered (28 files) and does not
> warrant a rewrite; targeted hardening only. pi to confirm/challenge and fill
> this section._

## 7. Explicit non-goals

- No big-bang rewrite. No MCP/API rewrite. No rewrite work on this branch.
- No framework lock-in decision before the framework-agnostic extraction (step 1).
