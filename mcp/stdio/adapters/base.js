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

  // Plan 4 (2026-05-25): runtime-native session discovery for fresh managed
  // launches where the env-read path returns null. Default returns null;
  // each concrete adapter overrides with its own discovery (filesystem scan,
  // SQLite query, gateway RPC, etc.).
  async discoverSessionId() {
    return null;
  }

  // ─────────────────── SYMMETRIC SESSION CONTRACT (Phase 2, 2026-05-30) ───────
  // Every adapter MUST advertise WHERE its session id comes from and HOW an
  // operator takes the session over (resident attach / resume). These two
  // members are part of the symmetric runtime contract — a new harness
  // implements the same triad and the symmetry-guard test (Task 2.2) iterates
  // the registry and fails loudly if any adapter omits them.
  //
  //   sessionIdSource ∈ {"pinned","captured","resume"}
  //     "pinned"   — id is a pure function of agentId (aify mints it; hermes).
  //     "captured" — the runtime mints its own id, aify captures it after the
  //                  fact (hook / log scrape; claude).
  //     "resume"   — id comes from a prior runtime session that aify resumes by
  //                  passing it back to the CLI (codex, pi).
  //
  //   resumeCommand(sessionId) — the operator takeover command string for that
  //     runtime (used by the dashboard resume button + the mode-FSM rejection
  //     error in Phase 4). Returns a string.
  //
  // The base defaults are intentionally LOUD: an unset `sessionIdSource`
  // (not a valid enum value) and a throwing `resumeCommand` make an adapter
  // that forgets to implement the contract detectable rather than silently
  // wrong. `ASYMMETRY(<rt>): <why>` comments document any per-runtime quirk.

  get sessionIdSource() {
    // Deliberately NOT one of the valid enum values — an adapter that fails to
    // override this is detectably broken (caught by the Task 2.2 symmetry guard).
    throw new Error(`abstract: ${this.name} adapter must override sessionIdSource`);
  }

  resumeCommand(_sessionId) {
    throw new Error(`abstract: ${this.name} adapter must override resumeCommand(sessionId)`);
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
