// runtimes-opencode.js — OpenCode runtime helpers (model splitting,
// permission config, response parsing). Extracted verbatim from runtimes.js
// (task #123). runtimes.js re-exports the public surface.

export function splitProviderModel(value) {
  const text = String(value || "").trim();
  if (!text || !text.includes("/")) return null;
  const [providerID, ...modelParts] = text.split("/");
  const modelID = modelParts.join("/").trim();
  if (!providerID || !modelID) return null;
  return { providerID: providerID.trim(), modelID };
}

export function opencodePermissionConfig(config = {}, executionMode = "managed") {
  if (config.permission && typeof config.permission === "object") {
    return config.permission;
  }
  const policy = String(config.approvalPolicy || "").trim().toLowerCase();
  if (policy === "never" || policy === "auto") {
    return { bash: "allow", edit: "allow", webfetch: "allow" };
  }
  if (policy === "ask") {
    return { bash: "ask", edit: "ask", webfetch: "ask" };
  }
  if (executionMode !== "resident") {
    return { bash: "allow", edit: "allow", webfetch: "allow" };
  }
  return undefined;
}

export function summarizeOpenCodeParts(parts = []) {
  const textChunks = [];
  for (const part of parts) {
    if (!part || typeof part !== "object") continue;
    if (part.type === "text" && part.text) {
      textChunks.push(String(part.text));
    }
  }
  return textChunks.join("").trim();
}

export function requireOpenCodeData(response, fallbackMessage) {
  if (response?.data) return response.data;
  const errorMessage =
    response?.error?.data?.message ||
    response?.error?.message ||
    fallbackMessage;
  throw new Error(errorMessage);
}
