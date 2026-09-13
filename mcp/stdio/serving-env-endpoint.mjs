// Which aify-env is serving this host: the one the launcher names, or a `herdr-aify env` daemon.
//
// THE BLIND SPOT THIS CLOSES, measured 2026-09-13 on the operator's host. The installed launcher bakes
// `AIFY_ENV_ENDPOINT="http://127.0.0.1:8802"`, and every env row asked that address. But a
// `herdr-aify env` runs a DEDICATED aify-env on a port the OS picks (57978 that day), so nothing
// listened on 8802 while a healthy daemon ran the operator's managed worker and claimed spawns:
// `spawn-delegation` read `unreachable`, `env-processes` and `env-code-currency` read `unknown`.
//
// THE DAEMON ALREADY SAYS WHERE IT IS. At readiness a dedicated aify-env writes `ready.json` into its
// invocation directory (`~/.aify/herdr/invocations/<id>/`, aify-env's `publishInstanceReady`), naming
// its `endpoint`, `pid` and `envInstance`. Invocations outlive their daemons, so a receipt is only a
// CANDIDATE: it counts when that endpoint's `/health` answers with the SAME pid and instance. A port
// the OS later handed to another process answers with a different identity and is not believed.
//
// THE LAUNCHER'S ADDRESS STILL WINS when it answers, so a host running aify-env the ordinary way is
// judged exactly as before. More than one live dedicated daemon is left unresolved rather than picked
// between: which of them a row describes would be a guess, and these rows exist to replace guesses.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

/** A receipt endpoint is only ever loopback: aify-env writes it that way, and a file must not aim a fetch elsewhere. */
const LOOPBACK_ENDPOINT = /^http:\/\/127\.0\.0\.1:\d{1,5}$/;

/**
 * Every readable `ready.json` under a herdr-aify profile root.
 *
 * @param {string} profileRoot  the directory holding `invocations/` (`~/.aify/herdr` on every host)
 * @returns {{invocation: string, pid: number, envInstance: string, endpoint: string}[]} empty when there are none
 */
export function readyReceipts(profileRoot, { io = { readdirSync, readFileSync } } = {}) {
  let invocations;
  try {
    invocations = io.readdirSync(join(profileRoot, "invocations"));
  } catch {
    return [];
  }
  const receipts = [];
  for (const invocation of invocations) {
    try {
      const receipt = JSON.parse(io.readFileSync(join(profileRoot, "invocations", invocation, "ready.json"), "utf8"));
      if (!LOOPBACK_ENDPOINT.test(String(receipt?.endpoint ?? ""))) continue;
      receipts.push({ invocation, pid: Number(receipt.pid), envInstance: String(receipt.envInstance ?? ""), endpoint: receipt.endpoint });
    } catch {
      // No receipt (never became ready) or an unreadable one: not a candidate.
    }
  }
  return receipts;
}

/**
 * The aify-env endpoint the env rows should ask, and whether it answered.
 *
 * @param deps.installed    the endpoint baked into the installed launcher, or ""
 * @param deps.receipts     `readyReceipts()` output
 * @param deps.fetchHealth  `GET {endpoint}/health` as parsed JSON, or null when it did not answer
 * @returns {{endpoint: string, answered: boolean, source: "installed"|"herdr-aify env"}}
 */
export async function servingEnvEndpoint({ installed, receipts, fetchHealth }) {
  if (installed && await fetchHealth(installed)) return { endpoint: installed, answered: true, source: "installed" };

  const live = [];
  for (const receipt of receipts) {
    const health = await fetchHealth(receipt.endpoint);
    if (health && Number(health.pid) === receipt.pid && String(health.instance ?? "") === receipt.envInstance) {
      live.push(receipt);
    }
  }
  if (live.length === 1) return { endpoint: live[0].endpoint, answered: true, source: "herdr-aify env" };
  return { endpoint: installed, answered: false, source: "installed" };
}
