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

  // ─────────────────── CONSOLE / WRAPPER (Plan 3 — server-side only) ───────────────────
  // wrapperName + consoleCommand are owned by the Python adapter package
  // (service/runtimes/) — used by service/routers/api_v2.py:_default_console_command
  // to build the dashboard Console launch command. Per the Plan 3 spec
  // "Specialize per language" decision (option A), the JS adapter doesn't ship
  // these. Keep throwing so accidental JS callers get a clear error.
  //
  // Same asymmetry for `is_resident_ready`: Python adapter overrides it
  // (claude checks channelEnabled, hermes checks gatewayUrl). The JS side
  // does NOT have an isResidentReady method — the server is authoritative
  // for resident-gate decisions via _default_capabilities_for. JS callers
  // should not gate on per-config readiness; the bridge's
  // defaultCapabilitiesForRuntime keeps a minimal inline hermes gateway
  // check (best-effort) but the canonical decision is server-side.

  get wrapperName() { throw new Error("not yet implemented: Plan 3 — server-side responsibility"); }
  consoleCommand(_opts) { throw new Error("not yet implemented: Plan 3 — server-side responsibility"); }

  // ─────────────────── DELIVERY (Plan 3) ───────────────────
  // controllerFor returns the runtime's controller instance for the given
  // dispatch opts, or null when the mode isn't supported. Subclasses
  // override; default raises so unimplemented adapters fail loudly.

  controllerFor(_opts) {
    throw new Error(`controllerFor is abstract — ${this.name} adapter must override`);
  }

  // Delegate methods route through whichever controller controllerFor returns.

  async injectMessage(opts) {
    const c = this.controllerFor(opts);
    if (!c) throw new Error(`No controller for runtime=${this.name} executionMode=${opts?.executionMode}`);
    return c.injectMessage(opts);
  }

  async interrupt(opts) {
    const c = this.controllerFor(opts);
    if (!c) return;
    return c.interrupt(opts);
  }

  async steer(opts) {
    const c = this.controllerFor(opts);
    if (!c) throw new Error(`Steering not available for runtime=${this.name}`);
    return c.steer(opts);
  }
}
