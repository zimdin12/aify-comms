# Lens C: dashboard (`service/new_dashboard/` and the routes it calls)

Read-only scan, 2026-09-25, at `b7fde7c8`. Nothing in the live dashboard was opened or clicked.
Evidence levels: **PROVEN** means reproduced by a scratch `node --test` probe against the real modules,
with a control run showing the same instrument can report the healthy case. **READ** means verified by
reading the cited lines, not executed. **ASSUMED** is labelled where it applies. The scratch probes are
not in the repo: they were run from the session scratchpad against the real modules under
`service/new_dashboard/`.

Baseline: `node --test inspector-forms.test.mjs run-inspector.test.mjs chat.test.mjs
message-history.test.mjs settings-panel.test.mjs agent-processes.test.mjs` reported 150 pass, 0 fail.
No existing test covers any of the defects below.

The reference pattern is `openCompactionHistory` (inspector-forms.mjs:192) and `openRunInspector`
(run-inspector.mjs:185), both fixed in v0.6.22. What they establish: keep the current content during a
refresh, set `loading`, and drop an answer that arrives after the drawer has moved on. Every drawer that
`refreshOpenInspector` (app.js:274) re-runs is re-opened on each `loadSlices` flush (slice-loaders.mjs:112)
and on each full cycle (refresh-cycle.mjs:186). So "on every refresh" below means every debounced
`data_changed` flush, which on an active fleet is often several times a minute.

---

### C1. Switching Settings tabs silently discards unsaved edits
- where: settings-panel.mjs:158-162 (`selectSettingsTab`) -> settings-panel.mjs:71 (`host.innerHTML = tabBar + panels`); design intent stated at app.js:410
- failure/cost: the operator edits a field on one tab, switches to a second tab, edits there and presses Save. The first edit is gone and nothing says so. The design comment says "ALL schema panels stay in the DOM so Save collects every field regardless of the active tab", but a tab click rebuilds every panel from `state.settings`, which erases the DOM values that `saveSettings` (app.js:421) would have read.
- evidence: READ. `selectSettingsTab` calls `renderSettings()`. The only guard in `renderSettings` (line 57) covers a focused input, and after a tab click the focus is on the tab button. The CSS already switches panels by class alone (`styles.css:1381-1382`, `.settings-panel {display:none}` / `.active {display:block}`), so no re-render is needed to switch.
- severity: P1 (operator input is lost silently, and the operator believes it was saved)
- effort: S
- fix sketch: have `selectSettingsTab` toggle `.active` on the tab buttons and panels plus `help-band`/`settings-save` visibility, without calling `renderSettings()`. Add a test: set a value in one panel, switch tabs, and assert the value is still in the DOM.

### C2. Message-detail drawer toasts "Message not found" on every refresh, and the "⋯" button fails on paged-in history
- where: inspector-forms.mjs:67-69 (`openMessageDetail` looks only in `state.messages`); app.js:303-304 (refresh re-opens it); chat.js:218 (the DM timeline renders `history.combined(state.messages)`); chat-render.mjs:177 (the "⋯" button is on every DM row)
- failure/cost: `state.messages` holds only the fleet's newest 80 rows (message-history.mjs:29). (1) Open a message's details. Once 80 more messages arrive anywhere in the fleet, every refresh fires a `role=alert` toast, "Message not found in the loaded set", and the drawer is left stale. (2) Pressing "⋯" on any message that was paged in by scrolling back shows only that toast. That applies to most of a busy DM: the module header measures 43 of 137 messages inside the window.
- evidence: PROVEN. The probe called `openMessageDetail('m1')` with m1 loaded, which set kind to `message` with no toast (the control). It then replaced `state.messages` and called the opener twice, the way `refreshOpenInspector` does. The result was two toasts, both "Message not found in the loaded set".
- severity: P1 (repeated alert spam from the same drawer-refresh class the operator just reported)
- effort: S
- fix sketch: look up the message in `history.combined(state.messages)`, or pass the row object in on click. On a refresh, if the message is gone, keep what is on screen and show no toast. Only a user click should toast.

### C3. A History or run drawer whose fetch fails flashes "Loading…" and the error on every refresh; the run drawer shows raw JSON
- where: inspector-forms.mjs:194-195 and :213-216 (the error path never sets `loaded`); run-inspector.mjs:189-199 (on a fresh open `previous.run` is null) and :214-216 (`<pre>{"error": …}</pre>`)
- failure/cost: v0.6.22 fixed the success path only. For a run that 404s (for example one pruned by retention) or a spawn-record fetch that keeps failing, each refresh repaints "Loading…" and then the error again. That is the same flicker the operator reported, now on the error path. The run drawer's error is a JSON blob.
- evidence: PROVEN. History with the server returning 500 wrote `[LOADING, ERROR, LOADING, ERROR]` over open plus one refresh. The control, with 200, wrote `[LOADING, OTHER, OTHER]`. The run inspector with a 404 wrote `[LOADING, <pre>{…}, LOADING, <pre>{…}]`, and the control wrote `[LOADING, RUN, RUN, RUN]`.
- severity: P2
- effort: S
- fix sketch: store `error` on `state.inspector` and treat "showing this drawer with an error" like `loaded`, so a refresh keeps the error on screen until an answer arrives. Render the run error as `<p class="subtle">Could not load run …: msg</p>`, as History does.

### C4. Run inspector: a refresh undoes "Load more", and a late "Load more" appends into a different run
- where: run-inspector.mjs:204-211 (a refresh always replaces `events` with page 1); :286-302 (`loadMoreRunEvents` has no still-current check and no catch); :304-312 (`toggleRunEventOrder` has no still-current check and no catch, and clears `events` before the await)
- failure/cost: the operator loads older events and the next data change truncates the list back to 50 and resets the scroll. If the operator opens run B while run A's "Load more" is in flight, A's events are appended under B's header. A failed load-more or order toggle only shows the generic "Unexpected error" toast (ui.js:136).
- evidence: PROVEN. After load-more there were 3 events; after one refresh there was 1, with `hasMore: true`. The race left run B's events as `["B-1","A-old"]` with `runId: rB`.
- severity: P2
- effort: S
- fix sketch: on a same-run refresh, fetch `limit = max(50, events.length)` (the server caps it, so page until covered) or merge by event id. Capture `runId` in `loadMoreRunEvents`/`toggleRunEventOrder` and bail if it no longer matches. Add catch plus toast.

### C5. The Fleet pulse sticks on "Loading fleet pulse…" after closing a conversation
- where: chat.js:413 (`close()` sets `pulse.data = null`, commented "force a fresh pulse fetch"); chat.js:423 (the 12 s throttle returns without fetching); chat.js:139 (an unforced `loadFleetPulse()`)
- failure/cost: open a DM from the pulse and re-click it within 12 s to go back. The pulse reads "Loading fleet pulse…" until the next `renderAll` after the throttle expires. With the socket up and the fleet quiet, that is the 60 s liveness tick (change-refresh.mjs:27) or later.
- evidence: PROVEN. Probe with a real `createChatController`: the first render fetched once and showed data (the control). Then open DM, `close()`, `refreshPulse()` left 1 fetch in total with the timeline still showing "Loading fleet pulse…".
- severity: P2
- effort: S
- fix sketch: in `close()`, either keep the last pulse data or call `loadFleetPulse(true)`.

### C6. The Environments page tells the operator to run `aify-comms`, which since v0.6.1 exits 2 and starts nothing
- where: environments-panels.mjs:232 (empty state: "Start an aify-comms bridge on a host"); environment-start-command.mjs:31,35 (emits `cd …\naify-comms <roots>`); environments-panels.mjs:345-350 ("Start command (run on the host to bring this bridge back)" plus "Copy start command"); the refusal is at install.sh:1505-1508
- failure/cost: the only recovery instruction the UI gives for a missing or dead host is a command the rendered launcher refuses: "this command starts nothing. The host tier is aify-env", exit 2. Before v0.6.1 the same paste superseded the live bridge and reaped the fleet (CLAUDE.md). "Stop bridge" and "Reset to bridge roots" also use the retired vocabulary.
- evidence: READ (the launcher template in install.sh was read, not run).
- severity: P2
- effort: S
- fix sketch: point the text and the copied command at `aify-env` (and `aify-env doctor`). Check what `data-env-control="stop"` now does against an aify-env-served environment, then rename or remove "Stop bridge". Doing that needs a decision from the aify-env owner; whether aify-env honours that control is ASSUMED unknown.

### C7. Message actions on paged-in history rows are silently broken
- where: chat-click-handlers.mjs:22-27 (Write reply: a lookup in `state.messages` only, and no else branch); message-actions.mjs:52-53 (Mark read/unread updates only the live copy); message-actions.mjs:64 (Unsend filters only `state.messages`); message-history.mjs:116 (`reset()` has no production caller; its only grep hit outside tests is its own definition)
- failure/cost: on a row loaded by scrolling back, "Write reply" does nothing. "Mark read" succeeds on the server, but the badge stays "unread" until a page reload. "Unsend" deletes on the server and the row stays visible for the rest of the session, so a second click 404s.
- evidence: READ. `chat.js:218` renders `history.combined(state.messages)`, and `MessageHistory.#rows` are never refreshed or pruned. The positive control for the `.reset()` grep: the same search found `term.reset()` in console-actions.mjs:87.
- severity: P2
- effort: M
- fix sketch: give `MessageHistory` `update(id, patch)`/`remove(id)` and have the three actions call it. Resolve the reply target through `combined()`.

### C8. A slice that fails during a full refresh is recorded as loaded and is not retried while the socket stays up
- where: app.js:264 (`fullyRefreshed(startedAt)` runs whatever each slice returned); change-refresh.mjs:114-115 (every slice's `loadedAt` is set); refresh-cycle.mjs:55-68 (allSettled keeps the last-good value)
- failure/cost: with the socket up, a failed `contracts`/`runs`/`messages`/`stats`/`settings`/`channels` fetch is refetched only when its tables next change, or on reconnect or tab re-show. The visible case: if `/settings/schema` fails at boot, the Settings page reads "Loading settings…" (settings-panel.mjs:58) indefinitely. Liveness slices recover within 60 s.
- evidence: READ. Nothing passes the settled statuses to `fullyRefreshed`.
- severity: P2
- effort: S
- fix sketch: return the failed slice names from `runRefreshCycle` and `want()` them with `RETRY_AFTER_MS`. The retry path already exists for partial refreshes.

### C9. The agent drawer refetches `/terminals?status=all` on every refresh, and the route reads every replay buffer to do it
- where: agent-drawer.mjs:155 (called from every refresh through app.js:292), contradicting agent-processes.mjs:22-24 ("FETCHED WHEN THE DRAWER OPENS, never polled"); routers/terminals.py:193 (`SELECT *`) and :204 (`terminal.pop("output")`), whose docstring (:160-162) says the output column is what makes a listing "tens of megabytes"
- failure/cost: while an agent drawer is open, every data-change flush costs up to 200 `terminal_sessions` rows, output blobs included, read from SQLite on the single-worker service and then thrown away. A second cost: `paintAgentDrawer`'s memo compares HTML that contains the rendered "Ns ago" label (agent-drawer.mjs:111, util.js:55-58), so a recently seen agent repaints on nearly every refresh.
- evidence: READ. The size of the output blobs is ASSUMED from the route's own docstring and was not measured.
- severity: P2
- effort: S
- fix sketch: select explicit columns without `output` in `list_terminals`. Load processes on open only, or when `sessions`/`agents` changed. Memo on `data-rel-ts` rather than the label text, which the ticker already keeps current.

### C10. The open chat conversation is rebuilt on any fleet-wide status change or message
- where: app.js:360 (the chat section signature includes `_agentSig()` and `_msgSig()` for the whole fleet); chat.js:368-371 (`render()` always calls `renderConversation`); chat.js:162-171 and :185 (`chat-conv-actions` innerHTML rebuilt); chat.js:245 (timeline innerHTML rebuilt)
- failure/cost: any agent anywhere changing status, or any message sent anywhere, rewrites the open DM timeline and its action bar. Text the operator is selecting to copy is deselected. On a channel, the "+ Add member…" select is reset before they can press Add. The rail has an HTML-equality guard (chat.js:109-111) and the timeline has none.
- evidence: READ.
- severity: P2
- effort: M
- fix sketch: give `renderConversation` the same last-HTML guard the rail uses, per element (title, actions, timeline). Skip rebuilding the actions while the add-member select has focus or a value.

### C11. The Analytics page shows zeros when `/analytics` fails, and its failure toast is unreachable
- where: analytics-page.mjs:33-39 (each fetch `.catch(() => null)`, so `Promise.all` never rejects; `data = {}`); :46-48 (a catch branch and toast that cannot run); analytics.js:169-174 and :221-224 (`{}` renders 0 Messages, 0 Runs, 0 Overdue, and so on)
- failure/cost: a first load that fails looks like a quiet fleet, with every KPI at 0 and no error. This goes against the repo's own "an error is not an empty list" rule (agent-processes.mjs:103). A later failure keeps last-good data without saying it is stale. Only usage has `usageStale`.
- evidence: READ.
- severity: P2
- effort: S
- fix sketch: keep `null` on failure, record `state.analytics.error`, and render "Could not load analytics (reason)" instead of the KPI grid. Add `dataStale` alongside `usageStale`.

### C12. Session rail rows cannot be operated from the keyboard
- where: session-rail.mjs:144 (`<article class="session-row" data-session-select>`, no `tabindex`/`role`); click-dispatch.mjs:310 (mouse only); keyboard-shortcuts.mjs (Enter/Space are handled only for `data-status-why`, `data-fav-toggle`, `data-diag-jump`)
- failure/cost: a keyboard user can tick a row's bulk checkbox but cannot open a session. The chat rail uses real `<button>`s (chat-render.mjs:113). `every-role-button-is-keyboard-operable.test.mjs` does not catch this because the row has no role.
- evidence: READ.
- severity: P2
- effort: S
- fix sketch: make the row body a `<button>` (as the chat rail does), or add `role="button" tabindex="0"` and include `[data-session-select]` in the Enter/Space handler.

### C13. The identity directory is rebuilt on every refresh
- where: identity-directory.mjs:62 (unconditional `innerHTML`); app.js:297-298
- failure/cost: the 9-column table sits in a 420 px drawer (styles.css:666) inside `.table-wrap` (overflow-x). Every flush resets the horizontal scroll to the left, which hides the Last-seen and Remove columns, and clears any text selection. The "Last seen" column uses `relTime` text, so the HTML changes over time even when the data has not.
- evidence: READ.
- severity: P3
- effort: S
- fix sketch: `paintIfChanged(host, html)` (drawer-paint.mjs:48), with `relTimeHtml` for the age as the agent drawer does.

### C14. The Sessions rail and filter chips are rebuilt on every render, on every page
- where: app.js:366 (`renderSessionWorkspace()` has no signature gate and no page check); session-rail.mjs:112 and :134 (unconditional innerHTML)
- failure/cost: every render (each WS-driven `renderAll`) rebuilds up to 80 session rows, even while the operator is on Chat. On the Sessions page, keyboard focus on a row checkbox or a filter chip is lost with each change, and a chip pressed with Space loses focus immediately because its own handler re-renders.
- evidence: READ. The CPU cost is ASSUMED small per render and was not measured.
- severity: P3
- effort: S
- fix sketch: add an HTML-equality guard on `session-rail` and `session-status-filter` (the same pattern as chat.js:109), and skip the rail when `page-sessions` is not active.

### C15. The Analytics page re-renders all nine panels on every render while throttled
- where: app.js:379 (`loadAnalytics()` on every `renderAll`); analytics-page.mjs:30-32 (the throttled branch still calls `renderAnalyticsPage()`); analytics-page.mjs:123-138
- failure/cost: every WS event rewrites the KPI grid and eight panels with the same data. Hover titles flicker and the work is wasted.
- evidence: READ.
- severity: P3
- effort: S
- fix sketch: in the throttled branch, return without rendering, or route the call through `renderSection` with a signature on `state.analytics`.

### C16. Raw JSON is shown as UI in three places, and two of them take over the drawer
- where: environments-panels.mjs:441 (a successful spawn opens the drawer as a `<pre>` JSON dump, with no success toast); agent-session-actions.mjs:52 (a mode-switch network error opens as a JSON drawer, with no toast); boot-wiring.mjs:314 (a failed paste upload opens the drawer over the chat the operator is typing in); app.js:798 (the `inspect` generic dump)
- failure/cost: the operator sees internal record shapes instead of a sentence, and loses the drawer they were using.
- evidence: READ.
- severity: P3
- effort: S
- fix sketch: use a toast for the two errors. On spawn success, toast "Spawn queued for X", and optionally switch to the spawn-requests table row.

### C17. The spawn-requests table shows the newest 200 of about 1,215 with no note, and the Sessions rail sends people there for "full history"
- where: environments-panels.mjs:248 and slice-loaders.mjs:75 (`limit=200`, `truncated` dropped); session-rail.mjs:132 and :159 ("Full history is under Environments")
- failure/cost: every other capped list here (sessions, runs, contracts, messages) says it is capped, and this one does not. The 1,215 figure comes from the e264804d commit message.
- evidence: READ.
- severity: P3
- effort: S
- fix sketch: store `spawnRequestsTruncated` and render the same `mb-warn` note that sessions use.

### C18. The agent Processes panel ignores `truncated`
- where: agent-processes.mjs:108 and :149 ("N terminal(s), M live" is presented as complete); routers/terminals.py:207 returns `truncated`
- failure/cost: an agent with more than 200 terminals is shown a partial list as the whole list. That is the "reads as that is everything" failure the route's own comment warns about.
- evidence: READ.
- severity: P3
- effort: S
- fix sketch: pass `answer.truncated` into `renderAgentProcesses` and append "(newest 200 shown)".

### C19. The Runs status filter forces the health chip to green "live", even when the filter fetch fails
- where: boot-wiring.mjs:92-103
- failure/cost: an amber "stale"/"reconnecting" chip is overwritten with green "live" by a filter change, and also by a filter failure. Its title keeps the previous state's text.
- evidence: READ.
- severity: P3
- effort: S
- fix sketch: on completion, restore the chip from `refreshChipState` (or leave it alone). Never set "live" from the catch.

### C20. The Work Loop State filter: poll-path toasts, a wrong-filter flash, and no still-current check
- where: refresh-cycle.mjs:73 then :80 (the open set is assigned, then the filtered set is awaited); work-loop-actions.mjs:43-48 (the toast fires from the poll path too; nothing checks the selection is still current)
- failure/cost: with "Failed" or "Answered" selected, (a) a render during the await briefly paints open contracts under that filter, (b) a failing fetch toasts "Load contracts failed" on every cycle, and (c) two quick filter changes can land out of order.
- evidence: READ. That a render actually lands inside the await window is ASSUMED: it needs a WS render in the gap.
- severity: P3
- effort: S
- fix sketch: skip assigning the base set to `state.contracts` when a non-open filter is active. Show the toast only on the user-initiated call. Drop an answer whose `v` no longer equals `#contract-state`.

### C21. Session stop and reset confirmations are not marked destructive and use the wrong words
- where: agent-session-actions.mjs:232 ("Really stop this session?" with the default tone); :267 (the bulk confirm reads "Really recreate N…" for a button labelled "Reset", session-rail.mjs:78); :216 ("Delete this session record?" is danger-toned but names no session)
- failure/cost: stopping or resetting live work gets the same neutral dialog as a harmless action. `a-destructive-confirmation-is-marked-destructive.test.mjs` checks only confirmations that guard a `DELETE`, so it does not see these.
- evidence: READ.
- severity: P3
- effort: S
- fix sketch: use `tone:'danger'` for stop and recreate, use the label "Reset" in the bulk text, and name the agent or session id in the delete confirm.

### C22. Inconsistent vocabulary
- where: index.html:449 (settings "Reset" discards unsaved edits) vs agent-drawer.mjs:66 and session-rail.mjs:78 ("Reset" means a fresh-context restart); work-loop-actions.mjs:178, 186-187 ("diagnostics item(s)", "Closed from Diagnostics") for the page the nav calls "Work" (index.html:44)
- failure/cost: one word names both a harmless and a destructive action, and the event text recorded on runs names a page that does not exist.
- evidence: READ.
- severity: P3
- effort: S
- fix sketch: rename the settings button "Discard changes". Use "Work" in the confirm and event text.

### C23. The agent drawer mixes destructive and benign actions, and "Open in Sessions" can open the wrong session
- where: agent-drawer.mjs:61-76 (Delete session and Remove agent sit between History and Open in Sessions); :75 renders `data-agent-open-sessions=""` when there is no session; session-click-handlers.mjs:45-49 then switches to Sessions with the previously selected session still showing
- failure/cost: a misclick risk on a 12-button wrap row, and a button that lands on another agent's session. agent-processes.mjs:138-140 already argues that such a button should be omitted.
- evidence: READ.
- severity: P3
- effort: S
- fix sketch: put danger buttons in their own trailing group. Render "Open in Sessions" only when `sid` is set.

### C24. The Files empty state says "No shared files" when Find hides them all, and when the first load fails
- where: shared-files.mjs:38 (`filtered()` applies the top-bar Find) and :49 (the empty-state text); :25-26 (the error is swallowed and the list stays `[]` on a first-load failure)
- failure/cost: a Find term that matches no file reads as "no files exist, upload one", and a failed first load reads the same way.
- evidence: READ.
- severity: P3
- effort: S
- fix sketch: if `state.files.length > files.length`, say "None match Find". Track a load error and say so.

### C25. Exports and methods that only tests use
- where: status.js:114 `renderStatusDot`; inspector-refresh.mjs:74 `shouldRefreshInspector`; realtime-dispositions.mjs:83 `ignoredReason`; ui.js:126 `asyncAction`; api-client.mjs:36 `currentApiBase`; message-history.mjs:116 `MessageHistory.reset`
- failure/cost: dead surface. Each has test coverage that makes it look alive. `setOperatorKey`, `resetAdoptionForTests`, `resetConsoleFindForTests` and `resetRefreshHistory` are deliberate test seams and are not counted.
- evidence: a node scan of all `export function|const|let|class` in non-test dashboard modules, with word-boundary matches across every other `.mjs`/`.js` in the directory. Negative control: an injected `zzNobodyUsesThis` was flagged. Positive control: `openAgentDrawer` was not flagged. There were zero exports with no reference at all. A repo-wide grep (`--include=*.mjs,*.js,*.html,*.py`, excluding node_modules and `*.test.mjs`) found only the declarations for the five names above.
- severity: P3
- effort: S
- fix sketch: delete them with their tests. For `reset()`, wire it or delete it; C7 may want an `update/remove` instead.

### C26. The Spawn form can be submitted twice
- where: boot-wiring.mjs:113-120 (the submit handler never disables the button); environments-panels.mjs:414-443
- failure/cost: a double-click posts two spawn requests for one agent id. What the service then does is ASSUMED (it may refuse the second or queue both).
- evidence: READ. The server-side outcome was not verified.
- severity: P3
- effort: S
- fix sketch: disable the submit button until the request settles.

---

## Checked and clean
- **Agent drawer, Processes panel still-current check**: `stillShowing` (agent-processes.mjs:196-201) drops a response for another agent or a closed drawer. `paintAgentDrawer` preserves the async panel across repaints (drawer-paint.mjs:24-39).
- **History drawer success path and superseded answers**: correct after v0.6.22. The probe control shows a refresh repaints no "Loading…".
- **Run inspector success path**: keeps the run and order across a refresh. The probe control shows `[LOADING, RUN, RUN, RUN]`.
- **Per-agent chat Analytics**: stale-response guard at chat.js:462-466, and a failure renders "Analytics unavailable for X" (chat-render.mjs:210).
- **Form drawers** (`agent-edit`, `continue`, `env-roots`) are never auto-refreshed (inspector-refresh.mjs:34-38). Editing focus suppresses refresh (app.js:280-284).
- **Spawn-form dropdowns**: protected from poll rebuilds while focused, and the runtime pick is preserved (environments-panels.mjs:23, :42-49).
- **Poll resilience**: `Promise.allSettled` with per-slice last-good values (refresh-cycle.mjs:55-70). The Files and spawn-request slices are fetched only when their page is open (refresh-cycle.mjs:54, :166).
- **Chat rail**: HTML-equality guard (chat.js:109-111). DM scroll anchoring on history prepend (message-history.mjs:187-191, chat.js:292-305).
- **Unhandled rejections are not silent**: a global toast catches them (ui.js:134-140). Close/Remind contract failures therefore surface, generically.
- **Destructive DELETE actions all confirm with danger tone** (remove agent, delete session, unsend, delete channel, remove member, delete file, stop terminal, stop worker, forget/stop environment), enforced by `a-destructive-confirmation-is-marked-destructive.test.mjs`.
- **Narrow-width CSS**: the inspector is `min(420px, 100vw)` (styles.css:666) with a 100vw sheet under 760 px (:1115). Tables sit in `.table-wrap` or wrap cells, and there are media rules at 1100/980/760/620/540/414 px. No horizontal page scroll was found by reading. This was not rendered, because opening the live dashboard could restore a saved DM and auto-mark messages read.
- **Dead modules**: none. Every non-test module is imported by production code (`no-dead-imports.test.mjs` plus the scan above).
