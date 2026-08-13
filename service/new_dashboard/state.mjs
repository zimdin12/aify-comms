// The dashboard's one mutable state object.
//
// Lifted out of `app.js` in v0.5.4. It is not moved because `state` was untidy where it was -- it is moved
// because it was the shared name that blocked everything else. `app.js` is one 4,900-line file whose
// subjects (the run inspector, the session workspace, the console, contracts, analytics) cannot be
// separated while each of them reads a name that only app.js declares: a module extracted from app.js
// cannot import `state` back from it, which is the upward import this series forbids everywhere, and here
// it would also be a cycle. With `state` here, both sides import DOWNWARD from a leaf.
//
// AN EARLIER MEASUREMENT SAID THIS WOULD NOT HELP, and it was wrong in an instructive way. It counted, per
// function, how many were blocked by something OTHER than `state` -- 122 of 164, by "calls to sibling
// app.js functions" -- and concluded the call graph, not `state`, was the binding constraint. But a call
// between two functions that move together in the same slice is not a blocker; counting it as one makes
// every cohesive cluster look welded in place. Measured as groups, `renderAll` reaches 54 functions and
// 1,484 lines needing nothing from app.js but `state`, `byId` and `apiBase`.
//
// WHY IT IS SAFE TO SHARE. `state` is a `const`: never reassigned, only mutated, by 26 functions. An ESM
// module body runs once and its exports are live bindings, so every importer sees THIS object -- the
// mutations keep landing in one place. The failure mode to fear is a SECOND declaration somewhere, which
// would not raise anything: the dashboard would render from one object while events updated another, and
// the symptom would be stale panels rather than an error. `state-identity.test.mjs` is the gate against
// that, and it exists because the Python side of this series caught exactly that fork (`_listen_events`,
// where two copies would have made `comms_listen` hang silently).
//
// The declaration below is byte-identical to the one that stood in app.js; only `export ` was added.

export const state = {
  loaded: false, // false until the first successful refresh — lets the chat rail show
                 // "Loading…" instead of "No agents." on a cold load.
  agents: [],
  contracts: [],
  // Work Loop layout: 'list' (flat) or 'board' (status columns). Persisted; the flat
  // list stays the default so nothing changes for anyone who doesn't opt in.
  contractView: (() => { try { return localStorage.getItem('aifyContractView') === 'board' ? 'board' : 'list'; } catch { return 'list'; } })(),
  messages: [],
  runs: [],
  sessions: [],
  environments: [],
  spawnRequests: [], // GET /spawn-requests — queued/claimed/failed/done spawns, surfaced on Environments.
  stats: {},
  files: [],
  // Plan 6 C3/C4/C5/C6: server settings snapshot (GET /api/v1/settings).
  // Mode-switch chips (Plan 6) and any other settings-gated UI consult
  // state.settings here. Empty object until first refresh completes.
  settings: {},
  terminalOwners: new Map(),
  activeXterm: null, // { terminalId, agentId, term, fitAddon, container } — xterm.js mounted into Session Console
  sessionTerminals: new Map(), // sessionId → most-recent terminalId seen for this session (cache prevents widget oscillation when the server clears runtime_state.virtualTerminalId mid-conversation per Bug #3 root cause)
  realtimeConnected: false,
  // Chat-first landing (Phase 1): conversation rail + timeline + composer state.
  chat: { identity: 'dashboard', selected: '', view: 'messenger', filter: '', liveOnly: false, openOnly: false, workingUp: false, unreadOnly: false, scope: 'all', statusFilter: new Set(), sortMode: 'activity', channels: [], channelMessages: {}, analytics: { agent: '', data: null }, pulse: { window: 60, data: null, loading: false, lastMs: 0 }, drafts: {}, replyTo: null, msgFilter: '', compact: false },
  selectedConversation: 'dashboard',
  selectedSessionId: '',
  selectedSessionTab: 'console', // Sessions = terminal-first (Console default); Activity is the read-only log
  selectedSessionIds: new Set(),
  selectedDiagnosticIds: new Set(),
  inspector: { kind: '', runId: '', source: '', run: null, events: [], hasMore: false, loadingMore: false, eventOrder: 'desc', sourceMessageId: '' },
  filter: '',
  runStatusFilter: '',
  runFromFilter: '', runToFilter: '', runRuntimeFilter: '', runSearch: '', // WS-H runs filters
  sessionStatusFilter: new Set(), // WS-F: status multiselect for the Sessions rail (empty = all)
  // Reveal the superseded (older, non-live) session rows the list collapses so one agent reads
  // as one entry. Off by default; the collapsed-count note toggles it. Without this the older
  // rows would be UNREACHABLE — and Delete session is only offered on a row you can see.
  showSupersededSessions: false,
  settingsTab: '', // active settings tab (empty → first group)
  // Global analytics page (WS-C). Lazily loaded when the page is first opened, then on refresh
  // while it stays active, and on range change. data === null until first load completes.
  analytics: { range: 'hour', data: null, loading: false, usage: null, consumption: null, usageStale: false, lastMs: 0 },
};
