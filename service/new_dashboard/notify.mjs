// Desktop notifications for messages addressed to the OPERATOR.
//
// Operator request: "i want dashboard to use notices system ... so i would hear when i would get
// message from agent."
//
// WHY THIS IS A PURE MODULE. `app.js` is ~4.9k lines and only reachable by source-regex tests,
// which cannot fail on wrong logic. Every decision here — should this message notify, is the tab
// focused, has this already fired — is a branch worth failing a test on, so it lives out here with
// `notify.test.mjs` and `app.js` keeps only the wiring.
//
// NO SERVER CHANGE IS NEEDED. The dashboard already holds a `/ws` socket, and the service already
// broadcasts `message_sent` with {id, from, to, subject} and `channel_message`.
//
// NO TLS IS NEEDED EITHER, on desktop: `localhost` is a secure context, so the Notification API
// works at http://localhost:8801 with zero infrastructure. (An earlier claim that HTTPS was
// required was wrong — that constraint applies to PHONES, which need a service worker; those are
// handled separately by the ntfy relay, not by this module.)
//
// THE HAZARD THIS MODULE IS SHAPED AROUND IS VOLUME. The fleet produced 3,883 messages in 14 days,
// in bursts of two-agent ping-pong at ~2-4 minutes per message. Notifying on fleet traffic would
// make the feature unusable within an hour and get switched off permanently. So the default is
// off, only operator-addressed messages qualify, a focused tab suppresses, and repeats coalesce.

export const OPERATOR_RECIPIENT = "dashboard";

// Two messages about the same thing inside this window raise ONE notification. Sized against the
// measured cadence (~2-4 min/message in an active thread): long enough to collapse a burst,
// short enough that a genuinely new message minutes later still reaches you.
export const COALESCE_WINDOW_MS = 90 * 1000;

// Events worth notifying about. `channel_message` is included because a channel the operator reads
// is still addressed at them; everything else on the socket is fleet mechanics.
export const NOTIFIABLE_EVENTS = new Set(["message_sent", "channel_message"]);

export const STORAGE_KEY = "aify.notifications.enabled";

function recipients(data) {
  const to = data && data.to;
  if (Array.isArray(to)) return to.map((t) => String(t || "").trim().toLowerCase());
  if (typeof to === "string") return [to.trim().toLowerCase()];
  return [];
}

// Is this event FOR the operator, as opposed to fleet chatter we can see but were not sent?
//
// `isChannelSubscribed` FAILS CLOSED by default, and that default is the point. The first cut
// returned true for EVERY `channel_message` on the reasoning that a channel the operator reads is
// aimed at them — but the dashboard can SEE every channel, not just the ones it joined, so that
// notified on channels the operator never subscribed to. Caught in review.
//
// Closed is the correct failure direction here: the dashboard's channel list loads asynchronously,
// so early in a session membership is simply unknown. Notifying-when-unsure would fire on
// everything for the first seconds after every reload — the volume failure this whole feature is
// shaped around — whereas staying quiet when unsure costs at most a missed notification for a
// message that is still sitting in the dashboard.
export function isForOperator(event, data, { isChannelSubscribed = () => false } = {}) {
  if (!NOTIFIABLE_EVENTS.has(String(event || ""))) return false;
  if (event === "channel_message") {
    const channel = String((data || {}).channel || "").trim();
    if (!channel) return false;
    try {
      return !!isChannelSubscribed(channel);
    } catch {
      return false;
    }
  }
  return recipients(data || {}).includes(OPERATOR_RECIPIENT);
}

// Stable identity for coalescing. Keyed on sender + subject rather than message id on purpose:
// the id is unique per message, so keying on it would coalesce nothing during exactly the
// ping-pong burst this exists to collapse.
export function coalesceKey(event, data) {
  const d = data || {};
  const who = String(d.from || d.channel || "?").trim().toLowerCase();
  const what = String(d.subject || "").trim().toLowerCase();
  return `${event}|${who}|${what}`;
}

export function buildNotification(event, data) {
  const d = data || {};
  const from = String(d.from || "agent").trim();
  if (event === "channel_message") {
    return {
      title: `#${String(d.channel || "channel").trim()} — ${from}`,
      body: String(d.body || "").slice(0, 180) || "(no body)",
    };
  }
  return {
    title: `${from} → you`,
    body: String(d.subject || "").slice(0, 180) || "(no subject)",
  };
}

/**
 * Decide-and-fire. Everything injectable so the tests drive real branches rather than mocks of
 * this module's own logic.
 *
 * Returns a short reason string instead of a bare boolean — when a notification does NOT appear,
 * "why" is the only question worth answering, and a boolean cannot answer it.
 */
export function createNotifier({
  notificationApi = typeof Notification !== "undefined" ? Notification : null,
  isEnabled = () => false,
  isFocused = () => true,
  now = () => Date.now(),
  coalesceWindowMs = COALESCE_WINDOW_MS,
  // Fails CLOSED by default — see isForOperator for why unknown membership must stay quiet.
  isChannelSubscribed = () => false,
} = {}) {
  const lastFired = new Map();

  return {
    handle(event, data) {
      if (!isEnabled()) return "disabled";
      if (!notificationApi) return "unsupported";
      // A visible dashboard does not need a popup — the operator is already looking at it.
      if (isFocused()) return "focused";
      if (!isForOperator(event, data, { isChannelSubscribed })) return "not-for-operator";
      if (notificationApi.permission !== "granted") return "no-permission";

      const key = coalesceKey(event, data);
      const t = now();
      const previous = lastFired.get(key);
      if (previous !== undefined && t - previous < coalesceWindowMs) return "coalesced";
      lastFired.set(key, t);

      // Prune so a long-lived tab does not grow this map without bound.
      for (const [k, ts] of lastFired) {
        if (t - ts > coalesceWindowMs * 10) lastFired.delete(k);
      }

      const { title, body } = buildNotification(event, data);
      try {
        // eslint-disable-next-line no-new
        new notificationApi(title, { body, tag: key });
      } catch {
        return "failed";
      }
      return "fired";
    },
  };
}

/**
 * Permission must be requested from a USER GESTURE, never on load — browsers reject or
 * permanently deny otherwise, and a page that asks unprompted is the reason people block
 * notifications site-wide.
 */
export async function requestPermission(notificationApi = typeof Notification !== "undefined" ? Notification : null) {
  if (!notificationApi) return "unsupported";
  if (notificationApi.permission === "granted") return "granted";
  if (notificationApi.permission === "denied") return "denied";
  try {
    return await notificationApi.requestPermission();
  } catch {
    return "denied";
  }
}

// Off by default. An opt-in that survives reload, and never throws on a storage-less context.
// Always a BOOLEAN. The first cut was `return storage && storage.getItem(...) === "1"`, which
// returns `null` — not `false` — when storage is absent, because `&&` yields the falsy operand
// rather than a boolean. Harmless in an `if`, wrong the moment it is compared or serialised.
export function readEnabled(storage) {
  if (!storage) return false;
  try {
    return storage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

// Returns whether the preference was actually PERSISTED. The first cut returned true when
// `storage` was absent — it skipped the write and reported success, so a context without
// localStorage would have shown a toggle that silently forgot itself on reload. Caught by its own
// test. Reporting success for work not done is the failure mode this repo keeps paying for.
export function writeEnabled(storage, enabled) {
  if (!storage) return false;
  try {
    storage.setItem(STORAGE_KEY, enabled ? "1" : "0");
    return true;
  } catch {
    return false;
  }
}
