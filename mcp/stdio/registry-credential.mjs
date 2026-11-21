// Comms discovers its registry entry; aify-env authorizes and reads the stored key.
import { credentialRefProblem, decodeCredential, resolveServiceKeySync, sameServiceEndpoint } from "aify-env/client/credentials";
import { SERVICE_NAME } from "./service-name.mjs";

export const CREDENTIAL_DIR_NAME = "credentials";
export const REGISTRY_ENV_NAME = "AIFY_SERVICE_REGISTRY";

// The HTTP transport uses this exact spelling too; only the adapter chooses loopback aliases.
export function transportBase(value) {
  return String(value || "").replace(/^(https?:\/\/)localhost(?=[:/]|$)/i, "$1127.0.0.1");
}

export function sameEndpoint(a, b) {
  return sameServiceEndpoint(transportBase(a), transportBase(b));
}

export function decodeCredentialBytes(bytes) {
  return decodeCredential(bytes).value;
}

export function registryEntryFor(registryText, serviceName) {
  try {
    const entry = JSON.parse(String(registryText ?? ""))?.services?.[serviceName];
    return {
      ref: typeof entry?.credentialRef === "string" ? entry.credentialRef.trim() : "",
      endpoint: typeof entry?.endpoint === "string" ? entry.endpoint.trim() : "",
    };
  } catch {
    return { ref: "", endpoint: "" };
  }
}

const refusalReason = {
  "endpoint-refused": "endpoint_mismatch",
  CREDENTIAL_INVALID: "credential_bytes_refused",
  CREDENTIAL_MISSING: "credential_unreadable",
  CREDENTIAL_UNREADABLE: "credential_unreadable",
  CREDENTIAL_INSECURE: "credential_custody_refused",
};

/** Return the stored key and its authorized transport base; diagnostics carry fixed reason labels only. */
export function keyForEndpoint({
  env = {}, readFile, join, homeDir = "", endpoint = "",
  serviceName = SERVICE_NAME, onRefusal = () => {},
} = {}) {
  const nothing = (reason) => {
    onRefusal(reason);
    return { key: "", source: "", endpoint: "" };
  };
  if (typeof readFile !== "function" || typeof join !== "function" || !homeDir || !serviceName) {
    return nothing("resolver_not_configured");
  }
  if (!endpoint) return nothing("endpoint_missing");

  const registryPath = String(env[REGISTRY_ENV_NAME] || "").trim() || join(homeDir, ".aify", "services.json");
  let entry;
  try { entry = registryEntryFor(readFile(registryPath), serviceName); }
  catch { return nothing("registry_unreadable"); }
  if (!entry.ref || credentialRefProblem(entry.ref)) return nothing("credential_ref_missing_or_invalid");

  const registered = transportBase(entry.endpoint);
  const result = resolveServiceKeySync({ endpoint: registered, credentialRef: entry.ref },
    { url: transportBase(endpoint), env: {}, home: homeDir, mode: "explicit-first" });
  return result.state === "ok"
    ? { key: result.value, source: "aify-env's credential store", endpoint: registered }
    : nothing(refusalReason[result.state] ?? "credential_refused");
}
