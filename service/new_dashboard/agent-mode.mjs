// Requested mode describes the next start, not a session that is still running.
import { ACTIVE_AGENT_STATUSES } from './status.js';
import { sessionRowIsLive } from './sessions-list.mjs';

/** Pure display/start projection. Registered mode remains untouched for runtime routing. */
export function agentModes(agent, sessions = []) {
  const registered = String(agent?.sessionMode || '').trim().toLowerCase();
  const definition = agent?.definition;
  const requested = definition?.state === 'defined' ? String(definition.mode || '').trim().toLowerCase() : '';
  const configuredMode = requested === 'managed' || requested === 'resident' ? requested : registered;
  const hasLiveSession = ACTIVE_AGENT_STATUSES.includes(String(agent?.status || '').trim().toLowerCase())
    || (Array.isArray(sessions) && sessions.some(session => agent?.id
      && String(session?.agentId || '') === String(agent.id) && sessionRowIsLive(session)));
  return {
    configuredMode,
    displayMode: hasLiveSession ? registered : configuredMode,
    pendingMode: hasLiveSession && registered && configuredMode !== registered ? configuredMode : '',
    hasLiveSession,
  };
}
