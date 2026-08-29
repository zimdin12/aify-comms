// The poll cycle. Every ~15 seconds the whole dashboard is rebuilt from this one function, and it
// was untested for the usual reason: it lived in app.js, where nothing can import it.
//
// It has ONE structural property that matters more than anything it renders — `Promise.allSettled`,
// not `Promise.all`. The single-worker service transiently drops a request under poll load, and with
// `Promise.all` one such blip rejected the whole refresh: no state updated, `renderAll` never ran,
// and the entire dashboard — every agent's status included — froze on its last render and read as
// stale. Six slices apply independently now, each keeping its last-good value.
//
// The six injected names are app.js's render orchestrator and its neighbours. They are injected
// rather than imported because each reaches `refresh`, and importing any one of them would pull the
// orchestrator into this module and undo the extraction.

import { api } from './api-client.mjs';
import { asAgentArray, asArray } from './record-fields.mjs';
import { chatLoadChannels, chatLoadConversation, loadInboxMessages } from './message-transport.mjs';
import { loadFiles } from './shared-files.mjs';
import { shouldLoadFiles, shouldLoadForPage } from "./files-page.mjs";
import { noteSliceFailure, refreshChipState } from './refresh-status.mjs';
import { refreshActiveTerminalTheme } from './settings-panel.mjs';
import { runQueryPath } from './run-helpers.mjs';
import { applyTheme } from './theme.js';
import { byId } from './ui.js';
import { state } from './state.mjs';

export async function runRefreshCycle({
  armRefreshTimer,
  chatController,
  evaluateFlowGates,
  loadContractsForState,
  refreshOpenInspector,
  renderAll,
}) {
  // Only flip the chip to "refreshing" if the cycle is actually SLOW (>500ms). Fast polls
  // (the common case) finish before this fires, so the chip stays a steady "live" instead of
  // flickering live↔refreshing every cycle.
  const slowChipTimer = setTimeout(() => {
    const c = byId('api-status'); if (c) { c.textContent = 'refreshing'; c.className = 'status-chip muted'; }
  }, 500);
  // RESILIENT POLL (2026-06-18): use allSettled, not Promise.all. The single-worker service can
  // transiently drop a request under poll load ("Failed to fetch"); with Promise.all ONE such blip
  // rejected the whole refresh → no state updated, renderAll never ran → the entire dashboard
  // (incl. every agent's status) froze on its last render and looked stale/"wrong". Now each slice
  // applies independently; a slice whose fetch blipped keeps its last-good value, and we always
  // re-render with whatever fresh data arrived this cycle.
  // The Environments page is the only reader of state.spawnRequests, and this slice is the LARGEST
  // thing the poll moves: 414,690 bytes of a 1,419,728 byte cycle measured against the live service
  // on 2026-08-26 -- 29% of it, for a table nobody is looking at. Same rule as /shared, same
  // fail-closed predicate.
  //
  // The SLOT is kept rather than the entry dropped: ok(i) and val(i) index this array by position,
  // so omitting one would silently shift every slice after it onto the wrong data.
  const wantSpawnRequests = shouldLoadForPage('environments');
  const settled = await Promise.allSettled([
    api('/agents'),                                                       // 0
    api('/contracts?limit=80'),                                           // 1
    Promise.resolve(null),                                                // 2 — fetched below, only if needed
    api('/messages/recent?limit=80'),                                     // 3
    api(runQueryPath()),                                                  // 4
    api('/sessions?limit=80'),                                            // 5
    api('/environments'),                                                 // 6
    wantSpawnRequests ? api('/spawn-requests?limit=200') : Promise.resolve(null), // 7
    api('/stats'),                                                        // 8
    api('/settings'),                                                     // 9
  ]);
  const ok = (i) => settled[i].status === 'fulfilled';
  const val = (i) => (ok(i) ? settled[i].value : undefined);

  if (ok(0)) state.agents = asAgentArray(val(0));
  if (ok(1)) { state.contracts = val(1).contracts || []; state.contractsBase = state.contracts; }
  // Keep a non-default Work-loop State filter alive across polls: the base fetch is
  // open-scope, so a terminal selection (Answered/Failed/Missing reply/…) emptied ~15s
  // after choosing it when the poll overwrote state.contracts (review finding #4).
  // contractsBase keeps the open set for the metrics; state.contracts follows the filter.
  const contractStateSel = byId('contract-state')?.value || '';
  if (ok(1) && contractStateSel && contractStateSel !== 'open') {
    try { await loadContractsForState(contractStateSel, false); } catch (_) { noteSliceFailure('contract filter'); /* keep base */ }
  }
  // messages: prefer recent, fall back to inbox, then keep prior — only touch if either succeeded.
  //
  // THE INBOX WAS A FALLBACK IN THE CODE AND NOT ONE ON THE WIRE. `/messages/recent` wins whenever
  // it returns a `messages` array, so on every healthy cycle the inbox response was fetched, parsed
  // and thrown away: 300,154 bytes of a measured 1,419,728-byte cycle, 21% of it, confirmed against
  // the running service. It is a pure read — the route's `peek=true` skips `_settle_inbox_read`
  // entirely — so not sending it changes nothing but the traffic.
  //
  // Its SLOT stays in the array, resolving null, because ok(i)/val(i) index by POSITION: removing
  // the entry would shift every slice after it onto the wrong data. The request is now made only
  // when the primary did not hand us messages, and it reports its own failure the way the other
  // out-of-band fetches do — a null slot resolves as `fulfilled`, so the chip would otherwise read
  // a fallback that failed as a slice that succeeded.
  const recentUsable = ok(3) && val(3).messages;
  let inboxMessages = null;
  if (!recentUsable) {
    try { inboxMessages = await loadInboxMessages(); } catch (_) { noteSliceFailure('inbox'); /* keep prior messages */ }
  }
  // `inboxMessages` IS THE WHOLE RESPONSE NOW, not the array. The loader returns it so the counts
  // travel with the rows; unwrapping here rather than there keeps the decision about WHICH list is on
  // screen in the one place that knows.
  const fallbackRows = inboxMessages?.messages || null;
  if (recentUsable || fallbackRows) {
    state.messages = recentUsable || fallbackRows || state.messages || [];
    // AND WHAT IT IS A PAGE OF, from whichever loader actually supplied it. The first version read
    // this off the inbox loader alone -- which runs only when `/messages/recent` returns nothing, so
    // the note it fed never rendered on a healthy service. Both endpoints report `truncated`; the
    // fallback also reports `showing`, and either way the count recorded is the list on screen.
    const source = recentUsable ? val(3) : inboxMessages;
    state.messageCounts = {
      showing: Number(source?.showing ?? state.messages.length) || state.messages.length,
      truncated: Boolean(source?.truncated) || Number(source?.total ?? 0) > state.messages.length,
    };
  }
  if (ok(4)) {
    state.runs = val(4).runs || [];
    // KEPT for the same reason as the sessions flag: under a capped page, "nothing matches" and
    // "none exist" are different facts that look identical.
    state.runsTruncated = Boolean(val(4)?.truncated);
  }
  if (ok(5)) {
    state.sessions = asArray(val(5), 'sessions');
    // KEPT, not dropped. Under a capped page, "no sessions match" and "none exist" are different
    // facts that look identical -- the same reason the contracts list keeps its flag.
    state.sessionsTruncated = Boolean(val(5)?.truncated);
    state.sessions.forEach((session) => {
      const terminalId = session.terminalId || session.terminal?.id;
      const agentId = session.agentId || session.agent_id;
      if (terminalId && agentId) state.terminalOwners.set(String(terminalId), String(agentId));
    });
  }
  if (ok(6)) state.environments = asArray(val(6), 'environments');
  // Guarded on the REQUEST, not just the result: a skipped slice resolves to null, and assigning
  // asArray(null) would wipe the list rather than leave it alone. Nothing reads it while the page is
  // closed, but wiping it would make the first render after opening flash empty.
  if (wantSpawnRequests && ok(7)) state.spawnRequests = asArray(val(7), 'spawnRequests');
  if (ok(8)) state.stats = val(8) || {};
  if (ok(9) && val(9) && typeof val(9) === 'object') {
    state.settings = val(9);
    applyTheme(state.settings); // apply the server-stored appearance (theme/palette/title)
    refreshActiveTerminalTheme(); // keep a mounted console's accent in sync
    armRefreshTimer(); // honor dashboard_refresh_seconds (no-op unless it changed)
  }
  try { await chatLoadChannels(); } catch (_) { noteSliceFailure('channels'); /* keep prior channels */ }
  // Keep an OPEN channel conversation live: channel messages are otherwise fetched only on
  // open/send, so the rail badge ticked up while the open timeline stayed frozen (review
  // finding #5). The conversation sig covers the re-render.
  if (String(state.chat.selected || '').startsWith('channel:')) {
    try { await chatLoadConversation(state.chat.selected.slice('channel:'.length)); } catch (_) { noteSliceFailure('conversation'); /* keep prior view */ }
  }
  // Stale-selection guard (review finding #10): if the open conversation's agent/channel was
  // removed (here or by another client), close back to the overview — otherwise the header,
  // timeline, and composer stay live against a dead entity and a send goes nowhere useful.
  {
    const sel = String(state.chat.selected || '');
    if (sel.startsWith('dm:') && ok(0) && !(state.agents || []).some((a) => a.id === sel.slice(3))) chatController.close();
    else if (sel.startsWith('channel:') && Array.isArray(state.chat.channels)
      && !state.chat.channels.some((c) => c && c.name === sel.slice('channel:'.length))) chatController.close();
  }
  // Only when someone can see it. `state.files` is read by the Files page alone, and /shared is
  // 113,854 bytes for 388 files (34,839 gzipped), fetched every cycle whether or not the page is
  // open: 8.0 MB an hour per tab at the default 15s refresh, 23.9 at the 5s floor. navigateToPage
  // loads it on open, so the page shows a fetched list rather than a cached one.
  if (shouldLoadFiles()) {
    try { await loadFiles(); } catch (_) { noteSliceFailure('files'); /* keep prior files */ }
  }
  // Only flip to "loaded" once the roster actually arrived: with the server fully down all
  // slices reject, and loaded=true made the rail show a misleading "No agents." while the
  // chip said reconnecting (review finding #12). Until then the rail keeps its loading state.
  if (ok(0)) state.loaded = true;
  evaluateFlowGates();
  renderAll();
  // Status chip: green while the CORE roster (agents) is fresh, even if a non-critical slice
  // blipped (don't alarm the operator over a transient). Only show "reconnecting" when the core
  // roster itself didn't refresh — we keep last-good and retry next cycle (no scary "API error").
  // OPERATOR-REPORTED 2026-08-11: "when i have inspector open and status changes, it does not
  // update." There was no refresh path at all — every opener rendered once and nothing re-rendered
  // it, so the drawer was a snapshot while the rows behind it moved. Re-render it here, per poll.
  //
  // Which drawers may be re-rendered is NOT decided inline: `inspector-refresh.mjs` owns that, fails
  // closed on any unclassified kind, and refuses while a form is open, focus is inside, or the
  // drawer's own fetch is in flight. Re-rendering a form would eat what the operator was typing,
  // which is a worse bug than a stale panel.
  refreshOpenInspector();
  clearTimeout(slowChipTimer); // cycle finished — cancel the pending "refreshing" flip
  // THREE STATES, because a sustained partial refresh is not a complete one. This used to read
  // 'live' in green whenever /agents succeeded, whatever else had failed -- and the poll keeps each
  // slice's last-good value, so a stale panel renders exactly like one where nothing changed.
  // refresh-status.mjs owns the rule, remembers the previous cycle so a single blip stays green,
  // and names which slices are stale rather than counting them.
  // The realtime flag is PASSED, not read inside: refresh-status.mjs is a pure module and reaching
  // into `state` from it would make its tests depend on a shared singleton. This is also the half
  // that was missing -- the flag had four writers and no reader at all.
  const chip = refreshChipState(settled, { realtimeConnected: state.realtimeConnected });
  const chipEl = byId('api-status');
  if (chipEl) {
    chipEl.textContent = chip.text;
    chipEl.className = chip.className;
    chipEl.title = chip.title;
  }
}
