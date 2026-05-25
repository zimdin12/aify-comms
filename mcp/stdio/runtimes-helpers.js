// runtimes-helpers.js
//
// Re-exports the subset of runtimes.js helpers that controllers (under
// mcp/stdio/controllers/) need. Controllers import from THIS file instead of
// directly from runtimes.js as a forward-compatible boundary: as Plan 3
// continues extracting per-runtime controllers (Tasks 8-11), each new
// controller can add helpers here without touching runtimes.js itself.
//
// The module-load cycle that originally forced a dynamic-import workaround
// in adapters/opencode.js (Task 7) is now broken at runtimes.js itself via
// setter-injection of adapterFor (see _registerAdapterFor in runtimes.js).
// This file does not import from adapters/, so it is safe to statically
// import from any controller, regardless of whether runtimes.js or
// adapters/index.js loads first.
//
// Future cleanup: actual helper bodies can migrate from runtimes.js into
// this file, shrinking runtimes.js incrementally.

export {
  // config / capability lookups
  getRuntimeConfig,
  controlCapabilitiesForRuntime,

  // prompt assembly
  buildSystemPrompt,
  buildUserPrompt,

  // opencode-specific
  opencodePermissionConfig,
  splitProviderModel,
  summarizeOpenCodeParts,
  requireOpenCodeData,

  // pi-specific
  detectPiRuntimeFailure,
} from "./runtimes.js";
