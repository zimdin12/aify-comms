// What to tell the operator when an edit of an agent became a request for its host.
//
// An agent aify-env defines is changed by its host (P0 C5): the service answers an edit of one with
// the request it queued, not the change. The toast says that, rather than that the change was made.
// PURE AND IMPORT-FREE, so its test calls it in Node.

/** Where the host's answer shows: queued is not done, and a host can refuse. */
export const OUTCOME = 'History shows what it did';

/** The note for a service answer carrying a queued `request`, or '' when the edit applied directly. */
export function requestedInAifyEnv(answer, agentId) {
  const request = answer?.request;
  if (!request) return '';
  // A LIFECYCLE ACTION (D9c): a defined agent's start, stop, restart or removal is queued for its host's aify-env.
  // `stop` is the operator's hold and `kill` the Stop that leaves the agent startable (operator, 2026-10-28).
  if (answer.queued === true && typeof request.action === 'string') {
    const verb = { start: 'Start', stop: 'Hold', kill: 'Stop', release: 'Release of the hold', restart: 'Restart',
      delete: 'Removal', spawn: 'Start' }[request.action] || 'Action';
    return `${verb} of ${agentId} queued for aify-env on ${request.machineId}; it happens when that host takes it. ${OUTCOME}`;
  }
  const what = request.patch?.remove === true ? 'Removal' : 'Change';
  return `${what} of ${agentId} requested in aify-env on ${request.machineId}; it applies when that host syncs. ${OUTCOME}`;
}
