import { api } from './api-client.mjs';
import { state } from './state.mjs';
import { renderResolvedStatusChip } from './status.js';

// Receipt status is not agent status. The service vocabulary is checked by the spawn-panel test.
const LIFECYCLE_TONES = new Map([
  ['pending', 'warn'], ['claimed', 'warn'],
  ['done', 'ok'], ['failed', 'bad'], ['refused', 'bad'],
]);

export function renderLifecycleSpawnStatus(status, outcome) {
  const tone = LIFECYCLE_TONES.get(status);
  return renderResolvedStatusChip({
    kind: tone ? status : 'unknown',
    label: status || 'unknown',
    tone: tone || 'muted',
    dotKind: tone === 'warn' ? 'working' : tone || 'unknown',
  }, outcome);
}

/** Keep the last good rows when the service cannot supply the history. */
export async function loadSpawnHistory() {
  try {
    const result = await api('/agent-lifecycle-requests/history/spawns');
    if (!Array.isArray(result?.requests)) throw new Error('Spawn history returned no list');
    state.spawnRequests = result.requests;
    state.spawnRequestsStatus = 'ready';
  } catch (error) {
    state.spawnRequestsStatus = 'unavailable';
    throw error;
  }
}
