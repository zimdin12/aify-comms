#!/usr/bin/env node
// Pure derivation of the STABLE per-agent hermes api_server session id.
//
// Both the hermes-channel.js sidecar and adapters/hermes.js import this so the
// pinned session id (`X-Hermes-Session-Id`) is byte-identical everywhere — the
// sidecar pins/drives exactly the session the adapter advertises. No randomness,
// no timestamps: same agentId → same id forever.

// Sanitize to hermes' safe session-id charset [a-zA-Z0-9_-]. Any other run of
// characters collapses to a single dash so distinct agentIds stay distinct
// enough for practical use while remaining a valid path/header value.
export function pinnedSessionId(agentId) {
  const safe = String(agentId || "")
    .replace(/[^a-zA-Z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `aify-${safe}`;
}

export default pinnedSessionId;
