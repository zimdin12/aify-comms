// aify-comms' roster read as definitions, with aify-env's own client and mapper (P0 C10).
//
// usage: node roster_as_definitions.mjs <aify-env checkout> <service url> <machine id>
// Prints the records on its last line as JSON.

import path from "node:path";
import { pathToFileURL } from "node:url";

const [repo, url, machineId] = process.argv.slice(2);
const load = (relative) => import(pathToFileURL(path.join(repo, relative)).href);
const { CommsApi, mintBridgeIdentity } = await load("lib/plugins/aify-comms/api.mjs");
const { importRecords } = await load("lib/plugins/aify-comms/agent-import-records.mjs");

const api = new CommsApi({ endpoint: url, credential: async () => "", identity: mintBridgeIdentity({ version: "e2e" }) });
console.log(JSON.stringify(importRecords(await api.agents(), machineId)));
