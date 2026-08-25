// Talking to the message and chat endpoints: sending a message, and loading the chat rail's data.
//
// Extracted from app.js in v0.5.4. `sendMessageWithTimeout` is the shared primitive and the reason these
// travel together — it is not chat-specific (run follow-ups use it too), so this module is named for the
// transport rather than for the chat page.
//
// THE TIMEOUT IS THE POINT OF THAT PRIMITIVE. A send that never settles leaves the composer disabled with
// no error, so the request is raced against an AbortController rather than left to the browser's default.

export async function chatLoadChannels() {
  try {
    // Pass the viewer id — /channels only computes per-channel unread_count when agentId is
    // supplied; without it every channel's unread badge was permanently 0.
    const res = await api(`/channels?agentId=${encodeURIComponent(state.chat.identity)}`);
    state.chat.channels = res.channels || res || [];
  } catch (_) { noteSliceFailure('channels'); /* keep prior list */ }
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
export async function sendRunFollowup(run, { retry = false, body = '' } = {}) {
  const target = runTargetAgent(run);
  if (!target) return;
  const text = body || run.body || run.summary || run.subject || `Follow-up for ${run.id}`;
  await sendMessageWithTimeout({
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
  });
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
