#!/usr/bin/env node
// Capability probe + loud assertion for the hermes-agent `api_server` platform.
//
// Historical failure mode: a hermes upgrade silently broke the delivery
// contract (daemon down, key rotated, endpoint renamed) and managed hermes
// delivery just no-op'd. probeApiServer turns each of those into a SPECIFIC,
// named reason; assertApiServer turns "unavailable" into a LOUD, fail-fast
// error at install/connect time instead of a silent no-op.
//
// Contract: docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md.

import { createHermesApiServerClient } from "./hermes-apiserver-client.js";

const CONTRACT_DOC = "docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md";

// Map a (non-ok) health result to a specific, actionable reason string.
function reasonFromHealth(res) {
  if (res.status === 401) return "api_server key mismatch";
  if (res.status === 404) return "api_server endpoint missing — hermes version drift";
  if (typeof res.status === "number") {
    return `api_server endpoint missing — hermes version drift (HTTP ${res.status})`;
  }
  // No HTTP status → the fetch itself failed (ECONNREFUSED, DNS, etc).
  const detail = String(res.reason || "");
  if (/ECONNREFUSED|connect|refused|fetch failed|ENOTFOUND/i.test(detail)) {
    return `daemon not running (${detail || "connection refused"})`;
  }
  return detail ? `api_server unreachable (${detail})` : "api_server unreachable";
}

// Probe api_server reachability. NEVER throws. Returns
// {available:true, version} or {available:false, reason:<specific>}.
export async function probeApiServer({ baseUrl, key, client } = {}) {
  const api = client || createHermesApiServerClient();
  try {
    const res = await api.health({ baseUrl, key });
    if (res && res.ok) return { available: true, version: res.version };
    return { available: false, reason: reasonFromHealth(res || {}) };
  } catch (error) {
    const detail = error?.message || String(error);
    return { available: false, reason: `daemon not running (${detail})` };
  }
}

// Given a probe result, assert the api_server is reachable. Throws a LOUD,
// explicit Error when unavailable; returns the version when available.
export function assertApiServer(probe) {
  if (probe && probe.available) return probe.version;
  const reason = (probe && probe.reason) || "unknown";
  throw new Error(
    `[hermes] FATAL: api_server unreachable (${reason}). ` +
      "Managed hermes delivery will not work. Run the hermes daemon " +
      "(API_SERVER_ENABLED=1 ... hermes gateway run) and re-run install.sh; " +
      `see ${CONTRACT_DOC}`,
  );
}
