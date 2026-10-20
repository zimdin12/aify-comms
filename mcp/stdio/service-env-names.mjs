// The environment names that carry where aify-comms is and what key opens it.
//
// A LEAF, moved out of `aify-service-endpoint.mjs` (which re-exports both), because that module resolves
// a credential when it is imported. The registration spec served at `/.well-known/aify-service` declares
// these names, and it is built at stamp time by a script that must read no credential at all.

/**
 * The environment names that carry the key opening that service, in precedence order.
 *
 * SAME ARGUMENT AS `ENDPOINT_ENV_NAMES` below -- named rather than pointed at, because this block had
 * to move above its own reader and a "see above" would then have pointed the wrong way. It was left
 * half-applied for a year. The per-server MCP env block is key-scoped,
 * so a key name this bridge reads but the registry does not declare is INHERITED from whatever
 * launched the runtime -- which for a credential is worse than an endpoint: one service's key reaches
 * another service's bridge and the request is accepted or refused for reasons nobody can see from
 * either side.
 *
 * Five modules typed this precedence out by hand. One list, read here and declared in the registry.
 */
export const API_KEY_ENV_NAMES = ["CLAUDE_MCP_API_KEY", "AIFY_API_KEY"];

/**
 * The environment names this bridge reads to find its service, in precedence order.
 *
 * Exported because something outside has to say the same thing and must not say it from memory: the
 * service registry declares which env names carry a service's endpoint, and a runtime's per-server MCP
 * env block is KEY-SCOPED — proven on Claude Code 2.1.236, where a per-server AIFY_SERVER_URL beat an
 * inherited value while an inherited AIFY_COMMS_URL passed through untouched. So a name this bridge
 * reads but the registry does not declare would be INHERITED from whatever launched the runtime,
 * quietly and correctly-looking, until two services disagree about where they point.
 *
 * One list, read here and declared there. Not a regex over this file, and not a second hand-typed copy.
 */
export const ENDPOINT_ENV_NAMES = ["CLAUDE_MCP_SERVER_URL", "AIFY_SERVER_URL"];
