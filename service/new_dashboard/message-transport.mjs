// Talking to the message and chat endpoints: sending a message, and loading the chat rail's data.
//
// Extracted from app.js in v0.5.4. `sendMessageWithTimeout` is the shared primitive and the reason these
// travel together — it is not chat-specific (run follow-ups use it too), so this module is named for the
// transport rather than for the chat page.
//
// THE TIMEOUT IS THE POINT OF THAT PRIMITIVE. A send that never settles leaves the composer disabled with
// no error, so the request is raced against an AbortController rather than left to the browser's default.

/**
 * Load the channel list. Resolves true when it loaded and false when it failed, keeping the prior
 * list; it never rejects, because several action handlers await it without a catch. The refresh
 * paths read the result so a failed load is retried rather than recorded as current (0.7.1 C4).
 */
export async function chatLoadChannels() {
  try {
    // Pass the viewer id — /channels only computes per-channel unread_count when agentId is
    // supplied; without it every channel's unread badge was permanently 0.
    const res = await api(`/channels?agentId=${encodeURIComponent(state.chat.identity)}`);
    state.chat.channels = res.channels || res || [];
    return true;
  } catch (_) { noteSliceFailure('channels'); /* keep prior list */ return false; }
}
export async function loadInboxMessages() {
  // The FALLBACK for /messages/recent, and until 2026-08-26 it was a fallback in name only: it sat
  // in the poll's allSettled array and was fetched EVERY cycle, then discarded whenever the primary
  // returned messages -- which is every healthy cycle. 300,154 bytes of a measured 1,419,728-byte
  // cycle, 21% of the payload, confirmed against the running service.
  //
  // Safe to skip because it is a pure read: the route's `peek=true` makes `_settle_inbox_read` a
  // no-op, so nothing is marked read by asking. Returns null when there is nothing usable, which is
  // the poll's signal to keep the messages it already had.
  //
  // It reports its own failure rather than leaving that to the call site -- the reason this module
  // owns the fetch at all. See out-of-band-loaders-report.test.mjs for the dead call-site reports
  // that rule replaced.
  try {
    const res = await api('/messages/inbox/dashboard?filter=all&peek=true&limit=80');
    // THE WHOLE RESPONSE, so the caller can read the counts it came with. Returning `messages` alone
    // is what left the surface unable to say it was showing a page; the poll cycle decides what to
    // record, because it is the half that knows WHICH loader supplied the rendered list.
    return res || null;
  } catch (_) { noteSliceFailure('inbox'); /* keep prior messages */ return null; }
}
export async function chatLoadConversation(name) {
  const res = await api(`/channels/${encodeURIComponent(name)}?limit=80&agentId=${encodeURIComponent(state.chat.identity)}`);
  state.chat.channelMessages[name] = res.messages || res.channel?.messages || [];
}
export async function chatSendMessage({ isChannel, target, identity, body, expectsReply, queueIfBusy, inReplyTo, type, priority, subject }) {
  if (isChannel) {
    // ChannelMessage requires from_agent + channel (the bare {from, body} 422'd). type/priority
    // ARE accepted by the model; subject/inReplyTo are not part of the channel contract.
    return api(`/channels/${encodeURIComponent(target)}/send`, {
      method: 'POST',
      body: JSON.stringify({
        from_agent: identity, channel: target, body,
        ...(type ? { type } : {}),
        ...(priority && priority !== 'normal' ? { priority } : {}),
        ...(queueIfBusy ? { queueIfBusy: true } : {}),
      }),
    });
  }
  // Explicit composer type wins; fall back to the expects-reply heuristic for back-compat.
  const finalType = type || (expectsReply ? 'request' : 'info');
  // Explicit subject wins; otherwise derive a short one from the body as before.
  const finalSubject = (subject && subject.trim()) ? subject.trim() : body.slice(0, 80);
  return sendMessageWithTimeout({
    from_agent: identity, to: target, type: finalType,
    subject: finalSubject, body, trigger: true,
    queueIfBusy: !!queueIfBusy, requireReply: !!expectsReply,
    ...(priority && priority !== 'normal' ? { priority } : {}),
    ...(inReplyTo ? { inReplyTo } : {}),
  });
}
// After a send throws, was anything stored? Only a refusal the service ANSWERED is a definite no: its
// ok:false envelope (`refused`) or a 4xx. A timeout, a lost connection or a 5xx may have landed after
// the commit, so resending blind can duplicate the message (review R5).
export function sendOutcomeIsUnknown(err) {
  if (err?.refused) return false;
  const status = Number(err?.status || 0);
  return !(status >= 400 && status < 500);
}

// A write counts as done only on its acknowledgement, `ok: true`. An empty 200 (api() answers {}) or a
// null proves nothing was stored, so it is an UNKNOWN outcome: thrown without a status, and the caller
// keeps the operator's text (review of 455fff8a).
export function requireAck(res, field = '') {
  if (res?.ok === true && (!field || res[field])) return res;
  throw new Error(field && res?.ok === true ? `the service acknowledged without a ${field}` : 'the service gave no acknowledgement');
}

// A send is acknowledged only with a usable message id: a non-blank string, kept as the service wrote it.
// [], {}, 7 or "  " pass a truthiness check and would clear the composer for a send nobody can find.
export function requireStoredMessage(res) {
  requireAck(res);
  if (typeof res.messageId === 'string' && res.messageId.trim()) return res;
  throw new Error('the service acknowledged without a usable message id');
}

// A follow-up threads to its run's message, so the service treats it as a REPLY: it never refuses
// it, and can answer ok:true while waking nobody (`notStarted` set, no dispatch run). Report that,
// rather than read "not refused" as "delivered" (review of dd992844). '' means it was dispatched.
function storedWithoutDispatch(res) {
  const notStarted = res?.notStarted || [];
  if (!notStarted.length || (res?.dispatchRuns || []).length) return '';
  return notStarted[0]?.reason || 'the agent cannot start live work now';
}

export async function sendRunFollowup(run, { retry = false, body = '' } = {}) {
  const target = runTargetAgent(run);
  if (!target) return { storedUnwoken: '' };
  const text = body || run.body || run.summary || run.subject || `Follow-up for ${run.id}`;
  const payload = {
    from_agent: 'dashboard',
    to: target,
    type: run.type || 'request',
    priority: run.priority || 'normal',
    subject: retry ? `Retry: ${run.subject || run.id}` : `Queue after ${run.id}`,
    body: text,
    trigger: true,
    queueIfBusy: true,
    requireReply: true,
    inReplyTo: run.messageId || run.message_id || '',
  };
  // From 0.9.8 the service stores a follow-up it cannot wake and lists it in notStarted, so this sends
  // once. A refusal stores nothing: thrown as definite, so the caller keeps the typed text.
  const res = await sendMessageWithTimeout(payload);
  if (res?.ok === false) throw Object.assign(new Error(res.error || 'refused'), { refused: true });
  return { storedUnwoken: storedWithoutDispatch(requireStoredMessage(res)) };
}
import { api } from './api-client.mjs';
import { noteSliceFailure } from './refresh-status.mjs';
import { runTargetAgent } from './record-fields.mjs';
import { state } from './state.mjs';

export async function sendMessageWithTimeout(payload, timeoutMs = 20000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await api('/messages/send', {
      method: 'POST',
      signal: controller.signal,
      body: JSON.stringify(payload),
    });
  } finally {
    clearTimeout(timer);
  }
}
