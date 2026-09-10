// Compare registration with this MCP bridge's launch identity. A child environment
// does not establish its parent's hook configuration, handle capture or live delivery.
// Managed sessions are excluded; this is an advisory for resident registration only.
import { cleanEnvPlaceholder } from './launch-identity.mjs';

export function residentIdentityWarning({
  registeredAgentId,
  envAgentId,
  sessionMode,
  runtime,
} = {}) {
  const mode = String(sessionMode || '').trim().toLowerCase();
  if (mode && mode !== 'resident') return '';

  const wanted = String(registeredAgentId || '').trim();
  const have = cleanEnvPlaceholder(envAgentId);
  if (!wanted || have === wanted) return '';

  const identity = have
    ? `this MCP bridge's launch AIFY_AGENT_ID is "${have}", not "${wanted}".`
    : `this MCP bridge has no usable launch AIFY_AGENT_ID for "${wanted}" (absent or unresolved).`;
  return ` WARNING: ${identity} Registration itself worked.`
    + ` Verify the intended identity in the runtime and MCP configuration, and check turn reporting`
    + ` and live delivery separately. This value alone does not establish the parent hook environment,`
    + ` a missing native session handle, or a need to relaunch.`
    + ` If a new launch is needed, use \`${wrapperFor(runtime)} --aify-agent ${wanted}\`.`;
}

function wrapperFor(runtime) {
  const r = String(runtime || '').trim().toLowerCase();
  if (r === 'codex') return 'codex-aify';
  if (r === 'hermes') return 'hermes-aify';
  if (r === 'claude-code' || r === 'claude') return 'claude-aify';
  return `${r || 'claude'}-aify`;
}
