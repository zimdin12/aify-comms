# Dashboard Next — Overhaul Round 2 (2026-06-17)

Operator after round 1: "still does not make me happy — the old one seems better currently."
Mandate: overall usability + design overhaul AND feature parity; make it genuinely better than
old `service/dashboard.html`; autonomous, take the time, report when done. Driven by 3 fresh
audits (sorting/filtering, exhaustive feature gap, UI/UX design critique).

## Root cause of "old feels better" (design audit)
1. **Needs-Attention strip renders on EVERY page** (`index.html` #attention-strip is a sibling of
   all `.page` blocks) → ~210px of mostly-zero counters pushed above content 6×. #1 density killer.
2. **Counter-grid proliferation** — attention strip + env-summary + diagnostics-summary + analytics
   stats stack 2–3 metric rows per page.
3. **No landing hero/identity** — old `.ops-hero` (gradient, live pulse) gave the app a face.
4. **Accent floods neutral data** — `--accent` drives runtime pills, heading underlines, scrollbars,
   etc.; under crimson the whole UI reads "on fire." Reserve accent for active/primary/focus.
5. Dead empty-state voids; loose vertical rhythm; tall list-cards lower scan density; Refresh styled
   as primary; lost 3-pane/context-rail layouts; settings lost per-tab descriptions.

## WS-G — Design overhaul (do FIRST; this is the "feel better" fix)
- [x] G1. Attention strip → landing only (Chat). Remove from sessions/envs/diagnostics/analytics/files/settings.
- [x] G2. One metric strip per page — fold env-summary/diagnostics-summary counts into page headers or demote.
- [x] G3. Landing hero on Chat (gradient surface, live status dot, 1–2 sentence pulse).
- [x] G4. De-accent neutral data: runtime/capability pills neutral; remove per-heading gradient underline;
      reserve accent for active nav / primary button / focus so crimson stays calm.
- [x] G5. Designed empty states (icon + sentence + CTA) for sessions main, attention list, work loop.
- [x] G6. Refresh → ghost/icon; exactly one solid-accent primary per page.
- [x] G7. Density: tighten session rows (8→6px, action on hover/overflow), compact runs/files rows,
      subtle gradient body background.
- [x] G8. Sessions status-filter chips lower-contrast when inactive; Settings per-tab description line.
- [x] G9. Chat message direction: distinct mine/others, sender initial/avatar.

## WS-H — Sorting / filtering parity
- [x] H1. Chat rail: sort selector (activity/oldest/name/name-desc/runtime/status), status multiselect +
      All/None/Live presets, unread-up + working-up toggles, open-only, compact, reset-view.
- [x] H2. Per-message search within a conversation (+ match banner); dedicated global message-search results.
- [x] H3. Runs: from-agent / to-agent / runtime filter selects + dedicated runs search (id/from/merged/target/subject/summary/error).
- [x] H4. Sessions: status All/None/Live presets + persist filter + group collapse + select-all (visible/stoppable/deletable).
- [ ] H5. Work Loop: category filter (direct/channel/self_wake) + missing state options + show-closed + priority sort.
- [x] H6. Global "Find": extend scope to sessions/agents (placeholder promises it).

## WS-I — Feature parity (top capability gaps, audit Top-20)
- [ ] I1. Mark read / mark-all-read (the `#mark-read` stub) + peek toggle. (HIGH — unread never clears today.)
- [ ] I2. Unsend / delete message (DELETE /messages/{id}).
- [ ] I3. Edit agent: rename / retarget env / edit workspace / set-clear session handle.
- [ ] I4. Environment controls: restart / stop / forget bridge; edit workspace roots.
- [ ] I5. Confirm / Keep session-id governance (session/confirm, session/keep).
- [ ] I6. Reset / recreate session (fresh-context) action.
- [ ] I7. Channel add / remove member.
- [ ] I8. Console mode inside Chat (flip a DM to live console in the chat pane).
- [ ] I9. Compaction / spawn-lineage history viewer.
- [ ] I10. Follow-up (reply-as-request); resident stop / resume-wake; mode-switch force-on-409 retry.
- [ ] I11. Correctness/affordance: copy-run-id execCommand fallback; image paste in CHAT composer;
      Enter-to-send + jump-to-bottom in chat; preview-reminders dry-run; settings discard/reset.

## Operating rules
- Browser-verify EACH chunk vs old (chrome-devtools, screenshot, console clean); rebuild new-dashboard + reload ignoreCache.
- Keep node+pytest green; commit per logical chunk (Co-Author: Claude Opus 4.8). Update checkboxes as shipped.
- Evaluate on BOTH default (teal) and the operator's crimson theme — accent must stay calm in both.
- Report only when WS-G + WS-H done and WS-I substantially done, after a final all-pages visual pass.
