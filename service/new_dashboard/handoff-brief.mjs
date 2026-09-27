// The handoff brief in the Compact / Continue-as form: fetched from the service, never built here.
//
// A handoff starts a fresh session, and its first message used to be `buildHandoffPacket`: the last 25
// message BODIES from whatever `state.messages` happened to hold, which was a different window and a
// different text from what `comms_compact` sent for the same agent. The operator's spec (2026-09-26)
// replaced both with one brief the service writes (`GET /agents/{id}/compact/handoff-brief`): the
// previous session, its native id and transcript location, and "read your last N messages" with
// comms_inbox. N is asked in the form; its default is the service's, shown once the brief arrives.
//
// THE PACKET STAYS EDITABLE, so a fill never overwrites what the operator typed: the automatic fill
// writes only into an empty box, and only the Rebuild button replaces text.

import { api } from './api-client.mjs';
import { sessionAgentId, sessionId } from './record-fields.mjs';
import { state } from './state.mjs';
import { byId, toast } from './ui.js';

/** The brief's URL. A blank count is left out so the service's default applies. */
export function handoffBriefPath(agentId, sid, recentMessages) {
  const query = new URLSearchParams();
  if (sid) query.set('sessionId', String(sid));
  const count = String(recentMessages ?? '').trim();
  if (count) query.set('recentMessages', count);
  const qs = query.toString();
  return `/agents/${encodeURIComponent(agentId)}/compact/handoff-brief${qs ? `?${qs}` : ''}`;
}

/** Fetch the brief for `agentId`'s session `sid`, asking for `recentMessages` (blank: the default). */
export async function loadHandoffBrief(agentId, sid, recentMessages) {
  return api(handoffBriefPath(agentId, sid, recentMessages));
}

/**
 * Put the brief for session `sid` into the open form. `force` replaces what is in the packet box (the
 * Rebuild button); without it an operator's text is left alone. Returns the brief, or null on failure.
 */
export async function fillHandoffBrief(sid, { force = false } = {}) {
  const session = state.sessions.find((s) => String(sessionId(s)) === String(sid));
  const agentId = session ? sessionAgentId(session) : '';
  if (!agentId) { toast('Session not found', 'warn'); return null; }
  let brief;
  try {
    brief = await loadHandoffBrief(agentId, sid, byId('cont-recent')?.value);
  } catch (err) {
    toast(`Could not load the handoff brief: ${err?.message || err}`, 'error');
    return null;
  }
  // The drawer may have moved on while the brief was in flight: only the form it was asked for, still
  // open, is written into.
  if (state.inspector?.kind !== 'continue' || String(state.inspector.sessionId) !== String(sid)) return brief;
  const packet = byId('cont-packet');
  if (packet && (force || !packet.value.trim())) packet.value = brief.text || '';
  const recent = byId('cont-recent');
  if (recent && !String(recent.value || '').trim()) recent.value = String(brief.recentMessages ?? '');
  return brief;
}
