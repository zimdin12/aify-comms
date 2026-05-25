// Abstract runtime adapter. Every supported runtime (claude-code, codex,
// hermes, pi, opencode) ships a subclass that fills in `name` and
// `sessionEnvVars` at minimum. The base class supplies shared session-handle
// normalization, model-override normalization, default diagnosticEnv()
// implementation, and stubs for the Plan 2 (capability) and Plan 3 (console +
// delivery) methods so the contract surface is defined upfront.

const HANDLE_PLACEHOLDERS = new Set(["unknown", "default", "none", "null"]);
const MODEL_PLACEHOLDERS = new Set(["unknown", "default", "auto"]);

export class RuntimeAdapter {
  // ─────────────────── IDENTITY ───────────────────

  get name() { throw new Error("abstract: subclass must override name"); }
  get displayName() { return this.name; }

  // ─────────────────── SESSION LIFECYCLE (Plan 1) ───────────────────

  get sessionEnvVars() { throw new Error("abstract: subclass must override sessionEnvVars"); }

  getCurrentSessionId() {
    for (const v of this.sessionEnvVars) {
      const raw = process.env[v];
      const normalized = this.normalizeSessionHandle(raw);
      if (normalized) return normalized;
    }
    return null;
  }

  normalizeSessionHandle(raw) {
    const text = String(raw == null ? "" : raw).trim();
    if (!text) return "";
    if (HANDLE_PLACEHOLDERS.has(text.toLowerCase())) return "";
    return text;
  }

  resumeArgs(handle) {
    const h = this.normalizeSessionHandle(handle);
    return h ? ["--resume", h] : [];
  }

  // ─────────────────── MODEL/CONFIG NORMALIZATION (Plan 1) ───────────────────

  normalizeModelOverride(raw) {
    const text = String(raw == null ? "" : raw).trim();
    if (!text) return "";
    if (MODEL_PLACEHOLDERS.has(text.toLowerCase())) return "";
    return text;
  }

  // ─────────────────── DIAGNOSTICS (Plan 1) ───────────────────

  diagnosticEnv() {
    const out = {};
    for (const v of this.sessionEnvVars) {
      const val = String(process.env[v] || "").trim();
      out[v] = val || "(unset)";
    }
    return out;
  }

  // ─────────────────── CAPABILITIES (Plan 2 — stubbed) ───────────────────

  get supportsResident() { throw new Error("not yet implemented: Plan 2"); }
  get supportsManaged() { throw new Error("not yet implemented: Plan 2"); }
  get supportsSteering() { throw new Error("not yet implemented: Plan 2"); }
  get supportsInterrupt() { throw new Error("not yet implemented: Plan 2"); }
  get supportsMultiClient() { throw new Error("not yet implemented: Plan 2"); }
  get preferredDeliveryMode() { throw new Error("not yet implemented: Plan 2"); }

  // ─────────────────── CONSOLE / WRAPPER (Plan 3 — stubbed) ───────────────────

  get wrapperName() { throw new Error("not yet implemented: Plan 3"); }
  consoleCommand(_opts) { throw new Error("not yet implemented: Plan 3"); }

  // ─────────────────── DELIVERY (Plan 3 — stubbed) ───────────────────

  async injectMessage(_opts) { throw new Error("not yet implemented: Plan 3"); }
  async interrupt(_opts) { throw new Error("not yet implemented: Plan 3"); }
  async steer(_opts) { throw new Error("not yet implemented: Plan 3"); }
}
