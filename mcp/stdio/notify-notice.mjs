// What the notify hook (notify-check.js) prints, as pure functions it can be tested through.
//
// THE HOOK NEVER CONSUMES A MESSAGE. It reads the inbox with peek, because a plain inbox read settles
// read receipts and can close claimed runs, and the hook cannot know that a model saw its output.
// Until 0.7.0 it read without peek and printed `systemMessage`, which Claude Code shows the USER and
// not the model, so messages were marked read that no model had read (v0.7 scan B1).
//
// Instead it remembers which ids it has already surfaced (`seenFile`), so an unread message is shown
// once rather than every ten seconds -- once PER SESSION. A relaunched model has seen none of them,
// and a seen-set kept per agent alone hid from it every message a previous session was shown.
//
// It shows at most `NOTICE_LIMIT` it has not shown, walking the unread population page by page until
// it has them (`collectUnseen`). Any FIXED window only moves the loss: reading the newest three hid a
// fourth, and reading the newest twenty hid a twenty-first, which stayed unread and was never
// surfaced while the newer ones stayed unread too (v0.7 review, twice).
//
// A LIVE CHANNEL SESSION GETS A QUEUED MESSAGE TWICE: here, and again when its channel run is claimed. So a Claude
// session started on a channel route asks the inbox to leave out every unread message waiting on a queued channel
// run (`queuedChannel=2`, service/api_core/queued_for_channel.py). The service excludes them in the query it pages,
// so the walk below pages only what remains; if a run later fails, its message is back in that population.
//
// What was left out is still NAMED, without bodies, so a channel that never claims cannot hide a message. The
// inbox returns the newest queued attempts as a window; each attempt is named at most once per session, in attempt
// order, against a watermark (`namedSince`) rather than a list of ids. A list has to forget, and a forgotten id came
// back once newer messages left the window (review of 2df43e00). An attempt older than one already named is counted
// in the cue's total, never named again; a requeued message is a new attempt, and is named again.

import { SAFETY_HEADER } from "./tool-response-format.mjs";
import { quoteUntrustedSubject } from "./quote-subject.mjs";
import { DEV_ROUTE, PLUGIN_ROUTE } from "./channel-producer.mjs";

const MAX_BODY = 800;
const SEEN_LIMIT = 200;
const INBOX_WINDOW = 20;
export const NOTICE_LIMIT = 3;

/** Is this hook running in a Claude session whose channel producer is live? The same route the producer reads,
 * and the runtime claude-aify exports, because codex-aify fires this hook too and could inherit a route. */
export function channelSessionOptsIn(env) {
  return env.AIFY_RUNTIME === "claude-code" && [DEV_ROUTE, PLUGIN_ROUTE].includes(env.AIFY_CHANNEL_ROUTE);
}

export function inboxUrl(serverUrl, agentId, offset = 0, { queuedChannel = false } = {}) {
  return `${serverUrl}/api/v1/messages/inbox/${encodeURIComponent(agentId)}?filter=unread&limit=${INBOX_WINDOW}` +
    `&offset=${offset}&peek=1${queuedChannel ? "&queuedChannel=2" : ""}`;
}

/**
 * The first `limit` unread messages this session has not been shown, newest first, and the unread
 * total. `fetchPage(offset)` returns one inbox page, or null for a refusal; a transport failure on the
 * FIRST page is thrown, so the caller can mark the service down, and on a later page ends the walk.
 *
 * It ends: every page either adds an id it has not looked at or stops the walk, which also stops it
 * against a service too old to know `offset` (it returns the first page again). And it is short: the
 * seen list holds at most `SEEN_LIMIT` ids, so `limit` unseen lie within the first
 * `SEEN_LIMIT + limit` unread, which is at most that many over `INBOX_WINDOW` pages.
 *
 * `queued` is the first page's queued-for-channel window, null when the service sent none, and `unreadTotal` its
 * count of every unread message, the queued ones included.
 */
export async function collectUnseen(fetchPage, seenIds, limit = NOTICE_LIMIT) {
  const looked = new Set();
  const fresh = [];
  let queued = null;
  let unreadTotal = 0;
  let total = 0;
  for (let offset = 0; fresh.length < limit; offset += INBOX_WINDOW) {
    let page;
    try {
      page = await fetchPage(offset);
    } catch (error) {
      if (offset === 0) throw error;
      break;
    }
    if (!page) break;
    total = Number(page.total) || 0;
    // A service older than version 2 sends no window, and did not leave the queued messages out: they are paged above
    // and keep their bodies, and nothing is named or counted from flags it may send instead.
    queued = page.queuedForChannel && Array.isArray(page.queuedForChannel.newest) ? page.queuedForChannel : null;
    unreadTotal = Number(page.unreadTotal) || 0;
    const newlyLooked = (page.messages || []).filter((m) => m?.id && !looked.has(m.id));
    if (!newlyLooked.length) break;
    for (const m of newlyLooked) looked.add(m.id);
    fresh.push(...unseen(newlyLooked, seenIds));
    if (offset + INBOX_WINDOW >= total) break;
  }
  return { fresh: fresh.slice(0, limit), total, queued, unreadTotal };
}

/** The ids already shown to THIS session; a record from another session, or none, is an empty set. */
export function seenForSession(stored, session) {
  if (!stored || typeof stored !== "object" || Array.isArray(stored)) return [];
  return stored.session === session && Array.isArray(stored.ids) ? stored.ids : [];
}

/**
 * What this session was already told about queued messages: `namedSince`, the newest ORDERED RUN it named, as
 * [requestedAt, runId], and `queued`, the count it was last told. A record of another session, or one written before
 * either existed (2df43e00 kept `cued` ids), tells nothing.
 *
 * THE WATERMARK ORDERS RUNS, NOT DELIVERIES. A run requeued after a failure keeps its requestedAt and id, a message
 * batched into a queued run joins that run's key, and a run arriving later at the same instant with a lower id
 * orders before the mark (the order of ids is a declared policy, not creation order). None of those is named;
 * each raises the queued count, which is what `nextCue` reports.
 */
export function cueMarkForSession(stored, session) {
  const own = stored?.session === session ? stored : {};
  const namedSince = Array.isArray(own.namedSince) && own.namedSince.length === 2 ? own.namedSince : null;
  return { namedSince, queued: Number.isInteger(own.queued) ? own.queued : 0 };
}

export function seenRecord(session, ids, mark = { namedSince: null, queued: 0 }) {
  return { session, ids, namedSince: mark.namedSince, queued: mark.queued };
}

const runKey = (entry) => [String(entry.requestedAt || ""), String(entry.runId || "")];
const after = ([at, run], [markAt, markRun]) => at > markAt || (at === markAt && run > markRun);

/**
 * Whether the session is owed a cue, which window entries it names, and the mark to keep. Owed when an entry's run
 * orders after the mark, or when more messages are queued than the session was last told: work that joined a run
 * already named is counted, and still reported. With no window (an older service), nothing is owed or changed.
 */
export function nextCue(window, mark) {
  if (!window) return { due: false, named: [], mark };
  const named = window.newest.filter((entry) => !mark.namedSince || after(runKey(entry), mark.namedSince));
  const namedSince = named.reduce((top, entry) => (top && !after(runKey(entry), top) ? top : runKey(entry)), mark.namedSince);
  const queued = Number(window.total) || 0;
  return { due: named.length > 0 || queued > mark.queued, named, mark: { namedSince, queued } };
}

/** What the session is told about bodies left out because a queued channel run will bring them: how many messages are
 * queued in all, who sent the newly named ones and their ids, never their text, and how to read any of them. */
export function queuedCueText({ named, queuedTotal, unreadTotal, agentId }) {
  const lines = named.map((m) => `- from ${m.from}, MessageId: ${m.messageId}`);
  const which = named.length
    ? `Newly queued, ${named.length} named here:\n${lines.join("\n")}`
    : "None is named here: the newer ones joined runs already named, or order before one.";
  return `aify-comms: ${queuedTotal} unread message(s) are queued for this session's channel and arrive as channel ` +
    `events, so their bodies are not repeated here (${unreadTotal} unread in this agent's whole inbox). ${which}\n` +
    `comms_inbox(agentId="${agentId}") lists every unread message, these included; ` +
    `comms_inbox(agentId="${agentId}", messageId="<id>") reads one.`;
}

export function unseen(messages, seenIds) {
  const seen = new Set(seenIds);
  return (messages || []).filter((m) => m?.id && !seen.has(m.id));
}

export function rememberSeen(seenIds, messages) {
  return [...seenIds, ...messages.map((m) => m.id)].slice(-SEEN_LIMIT);
}

function formatMessage(m, agentId) {
  const priority = m.priority && m.priority !== "normal" ? ` [${String(m.priority).toUpperCase()}]` : "";
  let body = String(m.body || "").trim();
  if (body.length > MAX_BODY) {
    body = `${body.slice(0, MAX_BODY)}\n...[truncated; call comms_inbox(agentId="${agentId}", messageId="${m.id}") for the full body]`;
  }
  return [
    `=== Message from ${m.from}${priority} ===`,
    m.subject ? `Subject: ${quoteUntrustedSubject(m.subject, 240)}` : "",
    `MessageId: ${m.id}`,
    "",
    "```\n" + body.replace(/```/g, "'''") + "\n```",
  ].filter((line, i) => line || i === 3).join("\n");
}

export function noticeText({ messages, total, agentId }) {
  const urgent = messages.filter((m) => m.priority === "urgent").length;
  const high = messages.filter((m) => m.priority === "high").length;
  const header = urgent
    ? `INCOMING: ${urgent} URGENT message(s). Process now before continuing.`
    : high
      ? `INCOMING: ${high} high-priority message(s). Read and address now.`
      : `INCOMING: ${messages.length} new message(s). Process these as part of your current work.`;
  const more = total > messages.length
    ? `\n\n...and ${total - messages.length} more unread (call comms_inbox to see them).`
    : "";
  return `${SAFETY_HEADER}\n\n${header}\n\n${messages.map((m) => formatMessage(m, agentId)).join("\n\n")}${more}` +
    `\n\nThese stay unread until you read them with comms_inbox or reply to them. When one owes a reply, reply via comms_send(from="${agentId}", ` +
    `to="<from-agent>", type="response", inReplyTo="<message-id>", ...) so the originator's run threads correctly.`;
}

// Claude Code and Codex both give the MODEL `hookSpecificOutput.additionalContext` on PostToolUse, and
// show `systemMessage` to the user only (Codex: learn.chatgpt.com/docs/hooks, "That additionalContext
// text is added as extra developer context"). Codex was sent `{ systemMessage: notice }` alone, so the
// notice never reached the model while the hook recorded it as shown (v0.7.2, external review item 5).
export function hookOutput(notice, { count }) {
  return {
    systemMessage: `aify-comms: ${count} new message(s) added to the agent's context.`,
    hookSpecificOutput: { hookEventName: "PostToolUse", additionalContext: notice },
  };
}
