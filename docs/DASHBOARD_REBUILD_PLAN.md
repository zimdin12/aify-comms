# Dashboard Rebuild Plan — the continuable plan (2026-06-10)

> **HISTORICAL COMPLETED PLAN.** The replacement dashboard shipped and is now the only
> operator UI. Use `service/new_dashboard/` for current behavior and `AGENTS.md` for current
> status/lifecycle semantics.

This plan superseded the planning content of the older docs and reflects a full 3-way audit of
(a) the old plan docs, (b) the live 8800 dashboard, (c) the WIP 8801 "Dashboard Next".

**Goal (operator's words):** a *really good* dashboard. The current one is functionally good
but is one ~8,900-line HTML file with architecture that makes it hard and risky for agents to
improve. The rebuild is a real upgrade — better architecture, prettier, more usable, more
mobile-friendly — not a 1:1 port. Some current features deliberately don't survive (see §4).

---

## 0. The core decision: build on `service/new_dashboard/` (8801)

The WIP preview is NOT junk. Audit evidence:
- The hardest subsystem — the interactive console — already works for **three runtimes**
  (xterm live PTY for managed; hermes tui_gateway iframe; codex synth console over app-server
  WS), chosen by a pure, **unit-tested** function (`chooseSessionConsoleWidget`, 16/16 tests
  passing today). It encodes two real production bugs (widget oscillation; wrapper-PTY-vs-synth
  preference) that took days of live debugging to earn. Restarting throws that away.
- Clean serving harness (own container, port 8801, pure frontend over the frozen 8800 API),
  consistent XSS escaping, real responsive design (3 breakpoints, mobile tab bar, 44px touch
  targets, swipe-to-close inspector), coherent dark theme with CSS variables.
- ~55-60% of the operator surface exists — and it's the hard 60% (sessions/console/runs/
  work-loop). The missing 40% is mostly CRUD-grade UI (channels, files, analytics, settings,
  dashboard-level chat).

**Not chosen:** greenfield (loses the console + bug knowledge); incrementally modularizing the
8800 monolith (its global-state + innerHTML + signature-cache architecture is the disease; the
audit counted 52 `window._*` globals, 389 functions, 5 independent hand-rolled render-dedupe
mechanisms, and a "bug museum" of compensation layers).

**8800 stays the stable default until the parity gate (§6) passes.** It keeps getting
bug fixes only — no new features on the monolith from now on (every new feature lands on 8801).

## 1. Doc disposition (what a continuing agent should read vs ignore)

| Doc | Status |
|---|---|
| **This file** | The plan. Start here. |
| `docs/DASHBOARD_8801_UX.md` | **Primary UX blueprint — read it.** Two amendments: (1) default landing = CHAT (the 2026-06-07 chat-first decision), not Sessions; (2) status chips = the 8-label contract (working/online/idle/available/blocked/stale/offline/stopped). Slices 1, 4, part of 5 and 11 are already shipped in `service/new_dashboard/`. |
| `docs/DASHBOARD_8801_PARITY.md` | **The active replacement gate (~90% applies).** Refresh needed per §6 below. F1/F2/F3 invariants are validated by a month of incident history — keep them verbatim. |
| `docs/WEB_APP_DESIGN.md` | Salvage only: Continue/compaction-packet UX, the spawn wizard, the Anti-Patterns list, frontend state rules. Its IA (Work/Runtime/Insight/System), status colors, and Home-page detail are SUPERSEDED. |
| `docs/DASHBOARD_ARCHITECTURE_PLAN.md` | Reference: the §0 two-path architecture diagram is the best single-page mental model; its "known issues" list is now a regression-pin checklist (mostly fixed on the monolith). |
| `docs/DASHBOARD_SPEC.md`, `docs/DASHBOARD_REVIEW.md` | Living docs for the 8800 dashboard (not rebuild plans). REVIEW's open items (inline modals instead of `prompt()`, group budgets, unread semantics) migrate into §5 backlog. |
| `docs/plans/dashboard_console_plan.md` | Executed/archived; its Ownership Model + Core Rule sections remain the console contract. |

## 2. Phase 0 — structural fixes FIRST (make incremental agent work safe)

Do these before any feature work; `app.js` is 2,210 lines and grew 6x in 9 days — one sprint
from a second monolith.

- [x] **0.1 ES modules + file split.** `<script type="module">`; split `app.js` into
  `api.js` (fetch + WS + auth), `state.js`, `status.js` (the canonical status resolver, F2),
  `console/{chooser,xterm,hermes,codex}.js`, `render/{sessions,runs,work,environments,...}.js`,
  `ui/{actions,toast,dialog}.js`. Tests then import pure helpers directly — delete the brittle
  regex+`new Function` extraction in `app.test.mjs`.
- [x] **0.2 Delete the dead code.** `renderAgents`/`renderMessages`/`renderConversations`/
  `renderAnalytics` (+ their orphaned event paths) target DOM ids that don't exist and
  null-crash anyone wiring them naively.
- [x] **0.3 Vendor xterm.js** (the index.html comment already promises it) — no CDN dependency;
  the console must work offline/LAN.
- [x] **0.4 Kill whole-app innerHTML re-render.** Per-section render keyed on input signatures
  computed in ONE place (not 5 hand-rolled ones); never re-render the console pane when its
  inputs are unchanged. This removes the disease that forced the terminalId cache + remount
  guard workarounds, and preserves scroll/focus/checkbox state for free.
- [x] **0.5 One async-action wrapper** for all delegated handlers (toast on failure — today
  several have unhandled rejections), and an inline dialog component replacing every
  `prompt()`/`confirm()`.
- [x] **0.6 Test harness:** keep node:test; add a thin DOM-less render-contract test per module
  (input state → HTML contains/escapes), and keep the "no hardcoded origins" server test.

## 3. Phase 1+ — feature slices to parity (each slice = ship + test + check off)

Order chosen so every slice is operator-usable immediately. The 8800 inventory's judgments
(§4) define what each slice INCLUDES vs drops.

- [x] **1. Chat as landing** (the biggest missing surface): conversation rail (DMs + channels)
  with presence dots (8 statuses), unread, favorites (server-side), identity switcher
  ("viewing as"), search; threaded timeline with reply/follow-up, read/unread, wake-vs-stored
  badge, message-detail; composer with send/queue, reply threading, artifact/image paste, and
  the **delivery-truthfulness toast ladder** (steered / queued-busy / console-delivered / woke /
  stored-offline) — that ladder is an operator-trust feature, port it verbatim.
  Simplifications vs 8800 (deliberate): collapse the 6-sort + 3-hoist + 9-bucket filter matrix
  to search + live/all + pin; one "expects reply?" toggle instead of raw type/priority selects
  (infer type); per-DM analytics behind an explicit inspector button (NOT the undiscoverable
  click-again-to-deselect gesture — it caused must-fix bugs twice).
- [x] **2. Granular WS consumption + conversation endpoint.** The server already broadcasts
  ~25 event types that 8800 throws away (everything collapses to a debounced full refetch with
  an N+1 per-agent inbox loop — ~26 requests/refresh at 15 agents). 8801 must consume
  `dispatch_updated`/`channel_message`/`agent_registered`/session events granularly. ONE
  additive server endpoint is justified here (the parity doc's freeze is "no changes *for*
  8801's sake" — this fixes 8800's N+1 too): `GET /conversations?identity=` returning rail
  summaries + unread counts in one call.
- [x] **3. Sessions: one canonical agent-detail surface.** Keep the agent-centric
  one-row-per-agent model + status multiselect + batch stop/delete + the 13 lifecycle actions —
  but as ONE "agent detail" drawer used by BOTH the sessions page and the chat inspector
  (8800 currently duplicates ~9 actions across three surfaces: Sessions menu, chat inspector,
  Identity Directory modal — the modal dies). Compact/Continue-as keeps the WEB_APP_DESIGN
  compaction-packet UX.
- [x] **4. Channels + Files.** Channels: create/join/leave/read + member management (in the
  chat rail, not a separate page). Files: simple list+upload+delete as a chat side panel.
- [x] **5. Control Room (slim) + Analytics merge.** One landing-overview page: ops strip,
  needs-attention (server-acknowledged dismiss — NOT the 8800 localStorage mute layer),
  working-now, recent flow. The Analytics page folds in as a tab (its SVG chart + range
  selector port over; drop the always-refetch-even-when-hidden behavior). Per-DM analytics
  already exists via `GET /analytics/agent/{id}`.
- [x] **6. Work Loop (simplified).** Contracts list + filters + close + reminders. The three
  self-repair buttons (repair delivered reads / repair handoffs ×2) exist because server
  bookkeeping drifted; the 2026-06-10 reaper/mirror fixes addressed the causes — verify, then
  DON'T port the buttons (keep the endpoints for emergencies). One hide mechanism (server
  close), not two.
- [x] **7. Settings + Help.** Settings: port the 4 tabs but AUDIT each knob (drop "Legacy
  Console injection" if the legacy path is gone; fix the stale "Default: 15" refresh hint;
  group bridge-policy vs dashboard-prefs). Help: do NOT bake a third copy of the status essay —
  link the canonical docs; keep only the quick-start + endpoint reference (generated or
  spot-checked against the router).
- [x] **8. Version badge + update awareness** (port from 8800: `GET /version`, behind-count
  warning pill).

> **Status (2026-06-16): all Phase 0 + Phase 1 slices shipped, browser-verified, committed.**
> Two intentional deviations from the plan text: (a) slice 2's optional additive
> `GET /conversations` endpoint was NOT added — the rail is built on the existing
> `/messages/inbox/{id}` + `/messages/recent` + `/channels` endpoints, keeping the 8800 API
> fully frozen; (b) slice 7's Help is a compact links + quick-start + endpoint-reference band
> on the Settings page (NOT a re-baked 5-tab essay), per the "don't bake a third status doc"
> instruction. Remaining before the §6 replacement gate: live multi-session round-trip soak +
> the operator's parity sign-off.

## 4. 8800 features that do NOT survive (the "doesn't make sense anymore" list)

- `renderHomeInsights`/`renderHomePanels` — dead code, never called, targets nonexistent DOM.
- The localStorage-only mute/dismiss/hide layers (muted issues, dismissed contracts) shadowing
  server state — replaced by server-side acknowledge/close.
- The Work Loop self-repair buttons as primary UI (fix causes server-side instead).
- The Identity Directory modal (duplicate agent-editing surface).
- The click-again-DM analytics gesture (data stays; gesture dies).
- The chat rail's 6 sort modes + 3 independent hoist toggles (collapse per §3.1).
- The Analytics page as a standalone nav item (merged).
- The in-app status-semantics essay (canonical docs instead).
- "Dashboard Refresh (seconds)" setting semantics (the 15s default text lies; under granular
  WS the poll is a slow safety net — present it as such or hardcode).
- `actionButton`'s `Function()`-eval of string handlers and the 2,500-entry expiring action
  map (replaced by the Phase-0 action wrapper).

## 5. Hard parts a continuing agent must NOT underestimate (from the 8800 audit)

1. **Console correctness rules** (each encodes a fixed bug — port behavior verbatim):
   monotonic `seq` dedupe seeded from the initial REST buffer; never diff/clear a live term on
   re-render (64KB sliding buffer ≠ stream history); paint-once-then-stream-deltas; explicit
   Refresh = the only full repaint; cached fetch FAILURE is not a cache hit; WebGL + CDN-fail
   fallbacks; debounced bidirectional resize; blocked input toasts (never swallows); clipboard
   execCommand fallback on plain-http origins; the truthfulness ladder for which banner+actions
   show (dead-session / resident-owned / no-PTY / env-offline); the WS fast path that bypasses
   full renders for terminal frames.
2. **Status vocabulary**: ONE resolver module (F2) consumed by every surface — never
   re-implement per page. 8 labels + the display nuances (booting console reads online; failed
   managed session reads available, NOT blocked — a deliberately-reverted decision; awaiting-
   reply tag; console-working lease).
3. **Multi-identity scoping**: unread/channels/read-marking/drafts/sends are all scoped to the
   viewing identity; peek mode suppresses auto-read.
4. **Product behaviors to keep from the monolith's compensation layer** (the layer itself
   dies): follow-bottom semantics, draft preservation per conversation, send-failure body
   restore, the 20s stuck-send watchdog.
5. **Mobile**: visualViewport `--app-height` (keyboards), rail/inspector as overlay sheets,
   the existing 8801 tab bar + touch targets are the right base.

## 6. The replacement gate (when 8801 becomes the default)

`docs/DASHBOARD_8801_PARITY.md` remains the gate, with these refreshes (apply to that doc):
- Status flow: the whitelisted vocabulary = the 8-label contract incl. `blocked`/`stale` +
  the display nuances in §5.2.
- Sessions flow: parity means the AGENT-CENTRIC model (one row per agent + status multiselect),
  not the old raw-rows page.
- New gated flows (8800 grew them post-freeze): chat-first landing + persisted UI state,
  per-DM analytics, version badge, delivery-truthfulness send toasts.
- Mechanics unchanged: F1 (single WS-driven client state), F2 (canonical status resolver),
  F3 (authoritative agentId+terminalId keying), per-flow flags, desktop+mobile both, CI
  assertions, operator sign-off, one-flag rollback (the 8800 dashboard stays deployed).

## 7. Working agreement for agents on this codebase

- Phase 0 completes before feature slices. No file may exceed ~500 lines; split instead.
- Every slice ships with module tests (pure helpers imported directly, no regex-eval).
- All interpolation through the shared `esc()`; attribute contexts included (the 8800 stored-XSS
  lesson). No `Function()`/string-eval handlers. No new `prompt()`/`confirm()`.
- Server changes only when they remove a structural client problem (like §3.2's conversations
  endpoint) — additive, parity-safe, tested on 8800's behalf too.
- The 8800 monolith is bug-fix-only from now on.
