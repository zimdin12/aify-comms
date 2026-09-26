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

import { SAFETY_HEADER } from "./tool-response-format.mjs";
import { quoteUntrustedSubject } from "./quote-subject.mjs";

const MAX_BODY = 800;
const SEEN_LIMIT = 200;
const INBOX_WINDOW = 20;
export const NOTICE_LIMIT = 3;

export function inboxUrl(serverUrl, agentId, offset = 0) {
  return `${serverUrl}/api/v1/messages/inbox/${encodeURIComponent(agentId)}?filter=unread&limit=${INBOX_WINDOW}` +
    `&offset=${offset}&peek=1`;
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
 */
export async function collectUnseen(fetchPage, seenIds, limit = NOTICE_LIMIT) {
  const looked = new Set();
  const fresh = [];
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
    const newlyLooked = (page.messages || []).filter((m) => m?.id && !looked.has(m.id));
    if (!newlyLooked.length) break;
    for (const m of newlyLooked) looked.add(m.id);
    fresh.push(...unseen(newlyLooked, seenIds));
    if (offset + INBOX_WINDOW >= total) break;
  }
  return { fresh: fresh.slice(0, limit), total };
}

/** The ids already shown to THIS session; a record from another session, or none, is an empty set. */
export function seenForSession(stored, session) {
  if (!stored || typeof stored !== "object" || Array.isArray(stored)) return [];
  return stored.session === session && Array.isArray(stored.ids) ? stored.ids : [];
}

export function seenRecord(session, ids) {
  return { session, ids };
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
