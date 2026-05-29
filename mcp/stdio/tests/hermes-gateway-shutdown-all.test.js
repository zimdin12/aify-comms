#!/usr/bin/env node
import assert from "node:assert/strict";
import { shutdownAllHermesGatewaySessions, __injectHermesGatewaySessionForTests, __hermesGatewayPoolSize } from "../hermes-managed-gateway-session.js";
const stopped = [];
__injectHermesGatewaySessionForTests("g1", { stop: async () => { stopped.push("g1"); } });
assert.equal(__hermesGatewayPoolSize(), 1);
await shutdownAllHermesGatewaySessions("test");
assert.deepEqual(stopped, ["g1"]);
assert.equal(__hermesGatewayPoolSize(), 0);
console.log("hermes-gateway-shutdown-all.test.js: all assertions passed");
