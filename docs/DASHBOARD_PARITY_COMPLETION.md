# Dashboard Next — Parity Completion Plan (2026-06-16)

Operator rejected the "done" claim: Settings far thinner than original, chat statistics
missing, pseudo-terminal console under-built, **theming/coloring entirely gone**, and overall
UI/UX flatter/less polished than the 8800 original. Mandate: reach **same-or-better** than the
old dashboard on UI/UX/design AND features, then report only when actually complete.

Source of truth: four code-grounded audits (Settings, Console, Analytics, Visual+Holistic).
Old dashboard: `service/dashboard.html`. New: `service/new_dashboard/`.

## Workstreams (ordered by operator-perceived impact)

### WS-A — Theming / coloring engine  ⭐ operator named "coloring options"
- [x] A1. Define missing CSS tokens in `:root` (`--line-soft`, `--accent-softer`, `--surface`,
  `--status-online`, `--accent-border`, derived `--accent-soft/shadow`) — several rules
  silently fall back today.
- [x] A2. Port 8 themes (`default/forest/violet/ember/ocean/graphite/crimson/indigo`) — CSS
  presets already in styles.css; wire `document.body.dataset.theme`.
- [x] A3. Custom 3-color palette (`dashboard_primary/secondary/tertiary_color`) → CSS vars, live preview.
- [x] A4. Appearance settings group: theme select + preview swatch tiles + 3 color pickers +
  dashboard_title; apply on load (localStorage + settings endpoint). New `type:'color'` + `type:'theme'`.

### WS-B — Settings full parity
- [x] B1. Add 11 missing keys to SETTINGS_SCHEMA (see audit C): `auto_confirm_session_id`,
  `managed_via_wrapper` (CSV→array), `contract_stale_hours`, `active_run_stale_minutes`,
  `active_managed_run_stale_minutes`, `idle_minutes`, `offline_minutes` (+ the 4 appearance keys via WS-A).
- [x] B2. Add `xhigh` to effort opts; add empty "OMP default" option to pi effort.
- [x] B3. Carry min/max bounds onto number fields.
- [ ] B4. (Optional) Advanced group for 4 backend-only knobs (or leave to Classic link).

### WS-C — Chat statistics / Analytics  ⭐ operator named "chat statistics does not exist"
- [x] C1. Global Analytics page: nav item + `#page-analytics` + loader for `GET /analytics?range=`.
- [x] C2. Traffic SVG chart (grid+axis+bars+area+line+dots) + range selector 24h/30d/12m/All + Total/Avg/Latest.
- [x] C3. Global stat cards (6) + health grid (6) + run-status-mix bars.
- [x] C4. Per-agent panel: restore `lastFailedSubject`; add rail click-again gesture.

### WS-D — Pseudo-terminal console  ⭐ operator named "pseudo terminal view"
Core xterm PTY already works (stream+input+resize). Missing the UX/robustness shell:
- [x] D1. Console toolbar: Copy (sel/all, execCommand fallback, Ctrl+Shift+C), Refresh/resync, Stop, Start console/Start fresh.
- [x] D2. Vendor + wire WebGL addon (perf) with context-loss fallback.
- [x] D3. Seq-based dedup + gap-resync on `terminal_output`.
- [x] D4. Blocked-input guard (toast instead of POST into void).
- [x] D5. Await-input pill + rail ⌛ badge; ResizeObserver re-fit; debounced/clamped resize; text fallback.

### WS-E — Visual polish (same-or-better look)
- [x] E1. Shadow/depth token tier on cards/panels/rows/metrics/rails/modals (P0).
- [x] E2. Hover-lift + bg/border shift on buttons/nav/rows; composerPulse; scroll-to-bottom FAB.
- [x] E3. Typography: h2 gradient underline, tiered weights; ambient `body::before` wash; nav shadow; blurred topbar.
- [x] E4. Pill taxonomy (type/priority/await/handoff/draft) color coding; directional chat bubbles (mine/system).
- [x] E5. Custom form controls (checkbox/select caret/scrollbars); segmented-control pill styling.

### WS-F — Feature gaps (un-sanctioned drops, audit F)
- [ ] F1. Compact / Continue-as-new-agent (compaction packet UX) + history. (Most operationally significant.)
- [ ] F2. Reply / follow-up threading (inReplyTo from composer + reply-context banner).
- [ ] F3. Draft preservation per conversation + send-failure body restore + stuck-send watchdog + follow-bottom.
- [ ] F4. Favorites toggle (star set/unset, fav hoist) — display-only today.
- [ ] F5. Sessions status multiselect filter + batch delete.
- [ ] F6. Agent drawer full lifecycle action set (delete/remove/clear/recover/compact/continue/edit) — 4→full.
- [ ] F7. Global message search (scan loaded message bodies).
- [ ] F8. Message-detail surface (inspector view for a single message).
- [ ] F9. Inbox Hygiene panel; bulk contract close; chat artifact-attach button.

## Operating rules
- Browser-verify EACH chunk against the old dashboard (chrome-devtools), screenshot compare, console clean.
- Rebuild `new-dashboard` container after each chunk; reload with ignoreCache.
- Keep tests green (node --test + pytest source-grep); add tests for new pure helpers.
- Commit per logical chunk with Opus 4.8 co-author. Update this doc's checkboxes as shipped.
- Report ONLY when all WS A–F complete + a final full visual/feature pass vs old.
