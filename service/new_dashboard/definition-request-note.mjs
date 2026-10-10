// What to tell the operator when an edit of an agent became a request for its host.
//
// An agent aify-env defines is changed by its host (P0 C5): the service answers an edit of one with
// the request it queued, not the change. The toast says that, rather than that the change was made.
// PURE AND IMPORT-FREE, so its test calls it in Node.

/** The note for a service answer carrying a queued `request`, or '' when the edit applied directly. */
export function requestedInAifyEnv(answer, agentId) {
  const request = answer?.request;
  if (!request) return '';
  // A LIFECYCLE ACTION (D9c): a defined agent's start, stop, restart or removal is queued for its host's aify-env.
  if (answer.queued === true && typeof request.action === 'string') {
    const verb = { start: 'Start', stop: 'Stop', kill: 'Stop', restart: 'Restart', delete: 'Removal', spawn: 'Start' }[request.action] || 'Action';
    return `${verb} of ${agentId} queued for aify-env on ${request.machineId}; it happens when that host takes it`;
  }
  const what = request.patch?.remove === true ? 'Removal' : 'Change';
  return `${what} of ${agentId} requested in aify-env on ${request.machineId}; it applies when that host syncs`;
}
