// Which configured channel server produces in a Claude session: claims dispatches, beats, writes the runtime marker.
//
// TWO SERVERS CAN BE CONFIGURED AND BOTH START. aify-comms' installer adds the user-scope `aify-comms-channel` server
// (claude-channel.js), which claude-aify's development-channels route loads. aify-wrapper's `aify-channel` plugin runs
// claude-channel-plugin.mjs, which its `--channels plugin:` route loads. Claude starts every configured server whether
// or not it admits that server's channel events (measured on Claude Code 2.1.308, aify-wrapper
// docs/evidence/2026-10-22-channel-plugin, cases D-F), and a server whose events are dropped still claims runs and
// marks them delivered, so the message is lost.
//
// So each server decides for itself, from the route claude-aify exported: the plugin's server only on the plugin
// route, the user-scope server on every other one. A session claude-aify did not start has no route and keeps today's
// producer, the user-scope server.

/** The route value claude-aify exports when it starts Claude with `--channels plugin:aify-channel@aify-local`. */
export const PLUGIN_ROUTE = "plugin";

/** The `source` Claude puts on a channel event, measured: a plugin's server is namespaced, the user-scope one is not. */
export const PLUGIN_CHANNEL_SOURCE = "plugin:aify-channel:aify-comms-channel";
export const USER_SCOPE_CHANNEL_SOURCE = "aify-comms-channel";

/** Is this server the session's producer? */
export function isThisRoutesProducer({ fromPlugin, route }) {
  return Boolean(fromPlugin) === (route === PLUGIN_ROUTE);
}

/** The source attribute this server's events arrive under. */
export function channelSource(fromPlugin) {
  return fromPlugin ? PLUGIN_CHANNEL_SOURCE : USER_SCOPE_CHANNEL_SOURCE;
}
