# New-dashboard polish — consistency audit + fix plan (2026-06-18)

Operator verdict: the new dashboard (8801) "still isn't polished — built in a hurry, not
presentable/usable; many small issues." Goal: make it **same-or-better than the old 8800
dashboard** (which is the in-use quality bar) so the operator switches to it. Old dashboard
= reference: `http://localhost:8800/api/v1/dashboard`.

> **Deploy note:** `new_dashboard/` is baked into the `new-dashboard` container image (NOT
> bind-mounted). After edits: `docker compose up -d --build new-dashboard`, then hard-reload
> the browser (ignoreCache) — assets cache aggressively.

## SHIPPED this round (commits, all pushed)
- `cf6ef25` status keepalive re-probe (#224). `cfecedb` composer Type/Priority/Subject. `5511c3a`
  Identity Directory + Spawn-requests table. `f3da47d` chat-as-hero + status legend.
- `098982c` settings form column (killed the label↔control gulf) + run-row overlap fix + dead
  idle/stale session filters. `9369e5e` composer meta hidden on channels.
- `3373124` **hermes TUI embeds INLINE** (managed hermes → gateway iframe, not a link-out) +
  de-duplicated session-detail header + clean custom checkboxes + horizontal action row.
- `df48e0e` chat rail rows show `role · status · preview` (8800 parity).
- `7d473c3` **design-token scale** (--control-h/-sm/-xs, --fs-xs/sm/md, --radius-*, --input-bg/
  --console-bg/--nav-bg/--border-hover) + fixed the `min-height:44px` control base → 34px +
  raised the most-visible sub-11px fonts.

## REMAINING (from the consistency audit — prioritized, NOT yet done)
1. **Apply the new tokens broadly** (the base fix is in; now propagate):
   - Collapse the 40/38/36px control overrides → `var(--control-h)`: styles.css ~207, 378, 380,
     427, 1127/1129/1131, 1168/1171, 1259, 1354, 1435, 1474, 1482, 1486; `.segmented button`
     (~713), and small chips (`.session-filter-chip` ~1450, `.filter-preset` ~1489,
     `.mode-switch-chip` ~1457, `.run-chip` ~533) → `var(--control-h-sm)`.
   - Map every "card" radius (8/9/10/11/12/13/14 → `--radius-card` 12) and control radius →
     `--radius-control`: chat-rail/conversation/msg (~1008/1055/1092), metric/band/item (~246/294),
     file-row, an-card, help-card, etc.
   - Replace remaining sub-11px em fonts with the type scale (`.msg-badge` .66em, outcome-n .68em,
     pulse-counts .74em, the .76–.78em cluster).
2. **Color tokens:** replace hardcoded `#0d1010` (input bg ~7×), `#0b0e13` (console ~6×), `#0b0f0e`
   (nav), `#465454` (hover border) with the new surface tokens; replace status-glow `rgba(84,197,139…)`
   /amber/red/blue literals with `color-mix(in srgb, var(--green) …)` so they re-theme. Fix the four
   different "primary text on accent" values (#061615 / #06110f / #fff / #1b0608) → `var(--accent-contrast)`.
3. **Merge duplicate selectors / delete dead rules:** `.chat-conv-head` (×2 ~1056/1118),
   `.chat-search-row` (×2 ~1015/1480), `.chat-rail-options` (×2 ~1042/1484), `.chat-msg-search`
   (×2 ~1120/1486), `.segmented` (×2 ~699/1423 — keep the pill version), `.sc` (×2). Remove the
   no-op `.composer{grid-template-columns}` (~862). Delete dead selectors: `.context-rail`, `.pane`,
   `.message`, `.rail-list`, `.agent-list`, `.agent` (confirm unused first).
4. **Spacing rhythm:** introduce `--gap`/`--pad-card` and apply to peer cards (metric/band/chat-msg/
   file-row/activity-row/an-card/help-card) + `*-list` gaps.
5. **Chat density (operator-flagged: rail, proportions, composer):**
   - Rail is over-stuffed (3 always-visible control rows: search+sort+gear / 6 scope+quick chips /
     6 status dots, in a 240–320px column). Move the 6 status dots into the ⚙ panel OR merge into
     one wrap-row. (Base done: chips legible now.)
   - Composer is the tallest stack (3 rows). Consider an "Options" disclosure like 8800 (tuck
     Expects-reply/Queue/Attach + maybe Type/Priority/Subject behind it) so the default composer is
     a clean textarea + Send. Base height fix already shortened the meta row.
   - `.chat-shell` is 2-col (rail | conversation); "Fleet pulse" renders inside the conversation
     pane as the empty state (NOT a 3rd column). Consider restoring a useful per-conversation
     inspector (8800's right column: Conversation/Session Governance/Runtime) — richer than global
     pulse for an open chat. (8800 reference.)

## Method
Audit subagent report (read-only) drove items 1–5; re-run a similar audit after the token
propagation to confirm the height/font/radius sets actually collapsed. Browser-verify each page
against 8800 before claiming done. Keep `console-chooser.test.mjs` + chat tests green.
