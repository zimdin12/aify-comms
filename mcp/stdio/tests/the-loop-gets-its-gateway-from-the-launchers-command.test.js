// The delivery loop gets its hermes gateway from the launcher's own command (0.9.10), the pinned aify-wrapper's
// `aify-hermes-gateway ensure <agent>`, and reads exactly one JSON line from it. Its stdout carries the gateway's
// token, so nothing it printed is ever repeated in a refusal. The command itself is a stand-in here; the last case
// checks the real one is where the loop will look for it.
import assert from "node:assert/strict";
import fs from "node:fs";
import { EventEmitter } from "node:events";
import test from "node:test";
import { GATEWAY_COMMAND, gatewayFromLaunch, gatewayFromLauncher } from "../hermes-gateway-source.mjs";

const TOKEN = "s3cret-token";
const ANSWER = JSON.stringify({ port: 9341, token: TOKEN, wsUrl: `ws://127.0.0.1:9341/api/ws?token=${TOKEN}` });

/** A stand-in command: prints `stdout`, then exits `code` (or fails to start with `error`). */
function command({ stdout = "", code = 0, error = null } = {}) {
  const calls = [];
  const spawn = (cmd, args, options) => {
    calls.push({ cmd, args, options });
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    setImmediate(() => {
      if (error) return child.emit("error", error);
      if (stdout) child.stdout.emit("data", stdout);
      child.emit("close", code);
    });
    return child;
  };
  return { spawn, calls };
}

test("A LIVE GATEWAY: the one JSON line becomes the loop's gateway, and the loop owns no process", async () => {
  const c = command({ stdout: `${ANSWER}\n` });
  const gateway = await gatewayFromLauncher({ agentId: "pc-manager", spawn: c.spawn, command: "/w/aify-hermes-gateway.mjs" });
  assert.deepEqual(gateway, { port: 9341, token: TOKEN, wsUrl: `ws://127.0.0.1:9341/api/ws?token=${TOKEN}`, child: null });
  const [{ cmd, args, options }] = c.calls;
  assert.equal(cmd, process.execPath);
  assert.deepEqual(args, ["/w/aify-hermes-gateway.mjs", "ensure", "pc-manager"]);
  assert.equal(options.stdio[2], "inherit", "its reasons land in the loop's own log");
  assert.equal(options.env, undefined, "it runs with the loop's environment, which is the launcher's");
});

test("NO GATEWAY: a refusal says so, and never repeats what the command printed", async () => {
  for (const [why, c, expected] of [
    ["a non-zero exit", command({ stdout: ANSWER, code: 1 }), /found no gateway for 'pc-manager' \(exit 1/],
    ["an answer with no url", command({ stdout: JSON.stringify({ port: 9341, token: TOKEN }) }), /answered no gateway url/],
    ["an answer that is not JSON", command({ stdout: `token=${TOKEN}` }), /answered no gateway url/],
    ["a command that cannot start", command({ error: Object.assign(new Error("spawn"), { code: "ENOENT" }) }), /could not run: ENOENT/],
  ]) {
    await assert.rejects(gatewayFromLauncher({ agentId: "pc-manager", spawn: c.spawn, command: "x" }), (error) => {
      assert.match(error.message, expected, why);
      assert.doesNotMatch(error.message, new RegExp(TOKEN), `${why} leaked the token`);
      return true;
    });
  }
});

test("THE LAUNCHER'S ANSWER: a real gateway url in the loop's environment is its gateway; anything else is none", () => {
  assert.deepEqual(gatewayFromLaunch({ AIFY_HERMES_GATEWAY_URL: `ws://127.0.0.1:9341/api/ws?token=${TOKEN}` }),
    { port: 9341, token: TOKEN, wsUrl: `ws://127.0.0.1:9341/api/ws?token=${TOKEN}`, child: null });
  for (const [why, value] of [
    ["unset", undefined],
    ["hermes' unexpanded placeholder", "${AIFY_HERMES_GATEWAY_URL}"],
    ["an http url", "http://127.0.0.1:9341/"],
    ["a url with no port", "ws://127.0.0.1/api/ws"],
  ]) assert.equal(gatewayFromLaunch({ AIFY_HERMES_GATEWAY_URL: value }), null, why);
});

test("THE COMMAND IS WHERE THE LOOP LOOKS: the pinned aify-wrapper beside this bridge ships it", () => {
  assert.match(GATEWAY_COMMAND.replace(/\\/g, "/"), /\/node_modules\/aify-wrapper\/bin\/aify-hermes-gateway\.mjs$/);
  assert.ok(fs.existsSync(GATEWAY_COMMAND), `the pinned aify-wrapper has no ${GATEWAY_COMMAND}: move the pin`);
});

test("THE ENTRY POINT HANDS THE LOOP THE LAUNCHER'S ANSWER: `run` started by hermes-aify reads its environment", () => {
  // The loop defaults to no launched gateway, so its tests never read the ambient environment; the entry point is
  // the one place that does, and without it every bring-up would ask aify-env again.
  const source = fs.readFileSync(new URL("../hermes-managed-host.js", import.meta.url), "utf8");
  assert.match(source, /^if \(IS_MAIN\) \{\n  runCli\(process\.argv\.slice\(2\), \{ launchedGateway: gatewayFromLaunch\(process\.env\) \}\)/m);
});
