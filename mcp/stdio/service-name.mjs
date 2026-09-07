// Who this service is, in the shared registry and to anyone resolving its credential.
//
// A MODULE FOR ONE STRING, and the reason is load rather than ceremony. `service-registry.mjs` owns
// this identity and says so: "a second hand-typed copy of an identity is how two files come to
// disagree about who you are." That owner also imports `aify-wrapper/lib/registry.mjs` to do its real
// job, so anything wanting only the NAME had to pay for the registry parser as well.
//
// `aify-http.mjs` is imported by every bridge process at startup, and this package is deliberately
// copied to a native directory because bridge cold-load time is load-bearing -- hermes gives MCP
// discovery a hardcoded 0.75s window. So the identity moved here, `service-registry.mjs` re-exports
// it (its ownership comment stays true, and nothing that imported it there has to change), and the
// runtime credential path can ask who it is without loading a parser it will never call.

//: The key this service owns in `~/.aify/services.json`, and the name its credential is filed under.
export const SERVICE_NAME = "aify-comms";
