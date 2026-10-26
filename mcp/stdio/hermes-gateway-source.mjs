// Where the delivery loop gets its hermes gateway (0.9.10): from the launcher's own command,
// aify-wrapper's `aify-hermes-gateway ensure <agent>`, which asks aify-env for this lifetime's gateway and starts
// one only when none is live. comms never starts, picks a port for, or kills a gateway.
//
// THE LOOP RUNS THAT COMMAND rather than asking aify-env itself, because a gateway the loop's recovery starts must
// carry the agent's environment, and the loop inherited the launcher's. The command is the pinned copy beside this
// bridge, the one `@@WRAPPER_DIR@@` names in the launcher, so both always run the same code.
//
// Its stdout carries the gateway's token, so it is parsed and never logged; its stderr goes to the loop's log.

import { spawn as nodeSpawn } from "node:child_process";
import { fileURLToPath } from "node:url";

export const GATEWAY_COMMAND = fileURLToPath(new URL("./node_modules/aify-wrapper/bin/aify-hermes-gateway.mjs", import.meta.url));

/**
 * The agent's live gateway, `{port, token, wsUrl, child: null}`: the loop never owns the process.
 * @param {{agentId: string, spawn?: Function, command?: string}} input
 */
export function gatewayFromLauncher({ agentId, spawn = nodeSpawn, command = GATEWAY_COMMAND }) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [command, "ensure", agentId], { stdio: ["ignore", "pipe", "inherit"], windowsHide: true });
    let out = "";
    child.stdout.on("data", (chunk) => { out += chunk; });
    child.on("error", (error) => reject(new Error(`aify-hermes-gateway could not run: ${error?.code || error?.message || error}`)));
    child.on("close", (code) => {
      if (code !== 0) return reject(new Error(`aify-hermes-gateway found no gateway for '${agentId}' (exit ${code}; its reason is in this log)`));
      let answer = null;
      try { answer = JSON.parse(out.trim().split("\n").pop()); } catch { /* refused below */ }
      if (!answer || !/^wss?:\/\//.test(String(answer.wsUrl || "")) || !Number.isInteger(answer.port)) {
        return reject(new Error(`aify-hermes-gateway answered no gateway url for '${agentId}' (${out.length} bytes; not shown, it carries the token)`));
      }
      resolve({ port: answer.port, token: answer.token, wsUrl: answer.wsUrl, child: null });
    });
  });
}

/**
 * The gateway the launcher verified moments before it started the loop, from the environment it exported, or null.
 * The loop's bring-up takes this instead of asking again: a second `prepare` whose probe missed a busy gateway would
 * end the one the visible TUI is attached to. The loop asks only to recover a gateway it cannot reach.
 * @param {Record<string, string | undefined>} env
 */
export function gatewayFromLaunch(env) {
  const wsUrl = String(env.AIFY_HERMES_GATEWAY_URL || "").trim();
  let url;
  try { url = new URL(wsUrl); } catch { return null; }
  const port = Number(url.port);
  if (!/^wss?:$/.test(url.protocol) || !Number.isInteger(port) || port <= 0) return null;
  return { port, token: url.searchParams.get("token") || "", wsUrl, child: null };
}
