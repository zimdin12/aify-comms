#!/usr/bin/env node
import assert from "node:assert/strict";
import { shutdownAllHermesSessions, __injectHermesSessionForTests, __hermesSessionPoolSize } from "../hermes-session.js";
const stopped = [];
__injectHermesSessionForTests("h1", { stop: async () => { stopped.push("h1"); } });
assert.equal(__hermesSessionPoolSize(), 1);
await shutdownAllHermesSessions("test");
assert.deepEqual(stopped, ["h1"]);
assert.equal(__hermesSessionPoolSize(), 0);
console.log("hermes-shutdown-all.test.js: all assertions passed");
