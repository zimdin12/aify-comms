// Which tables each part of the dashboard reads, and which parts show liveness.
//
// PURE AND IMPORT-FREE, so the service's own test can load it (`service/tests/
// test_the_dashboard_slices_name_every_table_they_read.py`) and check it against the tables each
// endpoint really reads, and so change-refresh.mjs can be tested without a DOM.
//
// THE MAP IS A SUPERSET ON PURPOSE. A table a slice reads and this map omits is a panel that silently
// stops updating; a table it lists and does not read costs one extra fetch. So each list errs wide,
// and that test fails when an endpoint reads a table its slice does not name.

/** Which tables each slice's endpoint reads. The keys are the slices this module can load. */
export const SLICE_TABLES = Object.freeze({
  agents: Object.freeze([
    'agents', 'agent_sessions', 'agent_turn_state', 'agent_status_state', 'agent_console_signal',
    'agent_tombstones', 'bridge_instances', 'claimer_leases', 'environments', 'terminal_sessions',
    'dispatch_runs', 'dispatch_controls', 'spawn_specs', 'spawn_requests', 'settings',
    // Unread counts: a message or a read receipt changes the roster.
    'messages', 'read_receipts',
  ]),
  contracts: Object.freeze(['dispatch_runs', 'dispatch_events', 'messages', 'read_receipts', 'agents', 'settings']),
  messages: Object.freeze(['messages', 'read_receipts', 'agents', 'dispatch_runs']),
  runs: Object.freeze(['dispatch_runs', 'dispatch_controls', 'dispatch_events', 'agents', 'messages']),
  sessions: Object.freeze([
    'agent_sessions', 'terminal_sessions', 'terminal_controls', 'agents', 'environments',
    'spawn_specs', 'bridge_instances',
  ]),
  environments: Object.freeze([
    'environments', 'environment_controls', 'bridge_instances', 'agents', 'agent_sessions',
    'terminal_sessions', 'spawn_requests', 'spawn_specs', 'settings',
  ]),
  spawnRequests: Object.freeze(['spawn_requests', 'spawn_specs', 'environments']),
  stats: Object.freeze([
    'messages', 'dispatch_runs', 'dispatch_controls', 'agents', 'channels', 'shared_artifacts',
    'read_receipts', 'agent_sessions', 'agent_tombstones', 'environments', 'spawn_requests',
  ]),
  settings: Object.freeze(['settings']),
  channels: Object.freeze(['channels', 'channel_members', 'messages', 'read_receipts']),
  conversation: Object.freeze(['messages', 'read_receipts', 'channels', 'channel_members']),
  files: Object.freeze(['shared_artifacts']),
});

/**
 * The slices that SHOW liveness -- an age, a heartbeat, a lease. A heartbeat touches `agents`, and
 * `agents` is also read by the contract and run lists, but no row there changes when an agent's
 * `last_seen` moves; refetching them once a minute for it would spend exactly what this saves.
 */
export const LIVENESS_SLICES = Object.freeze(['agents', 'sessions', 'environments']);

/** The slices that read any of `tables`, in SLICE_TABLES order. */
export function slicesReading(tables, among = Object.keys(SLICE_TABLES)) {
  const touched = new Set((tables || []).map((table) => String(table)));
  if (!touched.size) return [];
  return among.filter((slice) => (SLICE_TABLES[slice] || []).some((table) => touched.has(table)));
}
