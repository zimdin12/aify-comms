// A hermes agent's turn edges have one producer: the delivery loop's gateway turn detector.
//
// The tool bridge armed a second one from the same gateway URL. Each stamps its edge after an awaited gateway
// read, so a slow read posted an old idle with a newer time than the loop's working edge, and aify-env closed the
// working turn (review of bd829e7b, reproduced through the real detector, event route and state host). The
// launcher starts the loop wherever it exports the gateway URL, so the bridge's copy only ever duplicated it.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DEFINES = "hermes-gateway-turn-detector.js";

function armingFiles() {
  return fs.readdirSync(ROOT)
    .filter((name) => /\.m?js$/.test(name) && name !== DEFINES)
    .filter((name) => /\bstartHermesGatewayTurnDetector\s*\(/.test(fs.readFileSync(path.join(ROOT, name), "utf8")));
}

test("only the hermes delivery loop arms the gateway turn detector", () => {
  assert.ok(fs.existsSync(path.join(ROOT, DEFINES)), "control: the detector's module is where this looks");
  assert.deepEqual(armingFiles(), ["hermes-delivery-loop.mjs"]);
});
