// Desktop notifications: the enabled flag, the notifier, and the permission handshake.
//
// Moved out of app.js in v0.5.4 as ONE unit. `notificationsEnabled` is a module-level `let` that
// `toggleNotifications` assigns, so the flag and the function cannot be separated — a copy left behind
// would be read by the button and written by nobody, which reads as a toggle that does not stick.
//
// Every DECISION about whether to notify lives in notify.mjs and is unit-tested there. What is here is
// the state and the permission handshake, and the handshake has a constraint that is easy to lose: the
// permission prompt must come from a USER GESTURE. A page that asks on load gets the site denied
// permanently, and there is no way back from that except the browser's own settings.

import { createNotifier, readEnabled, requestPermission, writeEnabled } from './notify.mjs';
import { state } from './state.mjs';
import { toast } from './ui.js';


// Desktop notifications. All decisions (is it for the operator, is the tab focused, has this
// already fired) live in notify.mjs where they are unit-tested — this file keeps only the wiring,
// because app.js is only reachable by source-regex tests that cannot fail on wrong logic.
export let notificationsEnabled = readEnabled(typeof localStorage !== 'undefined' ? localStorage : null);

export const dashboardNotifier = createNotifier({
  isEnabled: () => notificationsEnabled,
  isFocused: () => typeof document !== 'undefined' && document.visibilityState === 'visible',
  // Channel notifications are MEMBERSHIP-gated (review finding): the dashboard can see every
  // channel, not just the ones it joined, so "any channel_message" would notify on traffic the
  // operator never subscribed to. Reads the same `members` array the chat UI already uses for
  // join/leave. Returns false while the channel list is still loading — notify.mjs fails closed
  // on purpose, and this is the source of that "unknown".
  isChannelSubscribed: (channel) => {
    const list = (state.chat && state.chat.channels) || [];
    const row = list.find((c) => String(c && c.name) === String(channel));
    return !!(row && Array.isArray(row.members) && row.members.includes('dashboard'));
  },
});

export async function toggleNotifications(on) {
  if (on) {
    // Must come from a user gesture — a page that asks on load gets denied permanently.
    const result = await requestPermission();
    if (result !== 'granted') {
      toast(result === 'denied'
        ? 'Notifications are blocked for this site — allow them in your browser settings.'
        : 'Notification permission was not granted.');
      return false;
    }
  }
  notificationsEnabled = !!on;
  writeEnabled(typeof localStorage !== 'undefined' ? localStorage : null, notificationsEnabled);
  return notificationsEnabled;
}
