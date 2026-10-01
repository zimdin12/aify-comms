// One run of an aify-env host's definition sync, with aify-env's own code, against a real service.
//
// usage: node definition_host.mjs <aify-env checkout> <service url> <definitions dir> publish|apply
//
// `publish` defines `lead` in a store at <definitions dir> and runs one sync pass: the host's first
// push. `apply` runs one pass as the same host coming back: it claims what the operator asked while it
// was away, applies it to the file, reports it, and pushes. Each run is a fresh process with a fresh
// bridge identity, as a restarted aify-env is, and says it is the claimer with one heartbeat first.
// Prints the sync's state as JSON on its last line.

import path from "node:path";
import { pathToFileURL } from "node:url";

const [repo, url, dir, phase] = process.argv.slice(2);
const load = (relative) => import(pathToFileURL(path.join(repo, relative)).href);
const { DefinitionStore } = await load("lib/agent-definitions.mjs");
const { CommsApi, mintBridgeIdentity } = await load("lib/plugins/aify-comms/api.mjs");
const { DefinitionSync } = await load("lib/plugins/aify-comms/definition-sync.mjs");

const ENVIRONMENT = "win32:e2e-host:default";
const MACHINE = "win32:e2e-host";
const INSTALLED = new Set(["claude", "codex", "hermes"]);

const api = new CommsApi({ endpoint: url, credential: async () => "", identity: mintBridgeIdentity({ version: "e2e" }) });
await api.heartbeat({ id: ENVIRONMENT, machineId: MACHINE, os: "win32", kind: "win32", cwdRoots: ["C:/work"], runtimes: [] });
const store = new DefinitionStore({ dir });
if (phase === "publish") {
  await store.set("lead", {
    name: "Lead", role: "coder", harness: "claude", mode: "managed", workspace: "C:/work",
    model: "m1", effort: "", instructions: "", env: {}, herdrSpace: true,
  }, { installed: INSTALLED });
}
const sync = new DefinitionSync({ api, store, installed: () => INSTALLED, machineId: MACHINE });
await sync.pass(ENVIRONMENT);
console.log(JSON.stringify(sync.state));
