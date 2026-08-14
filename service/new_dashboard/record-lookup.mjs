// Resolve a dashboard record by kind and id. Moved out of app.js in v0.5.4.
//
// The JSON inspector is handed a kind and an id from a data attribute and has to find the record again.
// An unknown kind must not throw — the attribute is written by many renderers — and messages are keyed
// differently from everything else, which is the quirk this exists to absorb.

import { state } from './state.mjs';

export function lookup(kind, id) {
  const maps = {
    agent: state.agents,
    contract: state.contracts,
    message: state.messages,
    run: state.runs,
    session: state.sessions,
    environment: state.environments,
  };
  return (maps[kind] || []).find((item) => String(item.id || item.messageId) === String(id));
}
