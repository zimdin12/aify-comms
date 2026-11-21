// The spec declares exactly the names the runtime reads, in precedence order.
// This data is also needed when stamping a checkout with no npm dependencies.
export const ENDPOINT_ENV_NAMES = Object.freeze(["CLAUDE_MCP_SERVER_URL", "AIFY_SERVER_URL"]);
export const API_KEY_ENV_NAMES = Object.freeze(["CLAUDE_MCP_API_KEY", "AIFY_API_KEY"]);
