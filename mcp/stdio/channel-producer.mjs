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
// route, the user-scope server only on the development route.
//
// NO ROUTE IS NO PRODUCER (2026-10-28). The installer registers the user-scope server in ~/.claude.json, so every
// Claude session on the host starts it, and with no route it used to produce: a plain `claude`, which no channel flag
// admits, carried about 1.1 KB of dispatch instructions on every turn and wrote a runtime marker. Activation is the
// route, never the agent's identity: a plain session can inherit an AIFY_AGENT_ID, and an opted-in session may start
// unbound and register later. A Claude started by hand with the development channel must export
// AIFY_CHANNEL_ROUTE=dev itself.

/** The route value claude-aify exports when it starts Claude with `--channels plugin:aify-channel@aify-local`. */
export const PLUGIN_ROUTE = "plugin";
/** The route value claude-aify exports when it starts Claude with `--dangerously-load-development-channels`. */
export const DEV_ROUTE = "dev";

/** The `source` Claude puts on a channel event, measured: a plugin's server is namespaced, the user-scope one is not. */
export const PLUGIN_CHANNEL_SOURCE = "plugin:aify-channel:aify-comms-channel";
export const USER_SCOPE_CHANNEL_SOURCE = "aify-comms-channel";

/** Is this server the session's producer? */
export function isThisRoutesProducer({ fromPlugin, route }) {
  return route === (fromPlugin ? PLUGIN_ROUTE : DEV_ROUTE);
}

/** The source attribute this server's events arrive under. */
export function channelSource(fromPlugin) {
  return fromPlugin ? PLUGIN_CHANNEL_SOURCE : USER_SCOPE_CHANNEL_SOURCE;
}
