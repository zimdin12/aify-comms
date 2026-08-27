// How much subscription quota is left, per pool.
//
// One MCP tool, `comms_usage`. v0.5.4 layer 2 of the server.js decomposition — the last tool with no
// remaining dependency on anything unowned.
//
// IT IS ADVISORY AND SAYS SO. The answer is meant to let an agent hand work to a pool with headroom, not to
// gate anything: a pool near 0% is a reason to route elsewhere, not a permission check. Nothing enforces
// quota from here.
//
// WHAT MAKES IT WORTH ITS OWN FILE IS THE UNKNOWN-VS-ZERO PROBLEM. Every number it prints can legitimately
// be absent — the collector warms up, a source can go stale, a token can stop answering — and a missing
// percentage rendered as "0%" would tell an operator a pool is exhausted when in fact nothing is known
// about it. So `left_pct == null` prints "?", and `stale` and `unknown` are printed as their own tags
// rather than folded into the number. That distinction is the same one `aify-comms doctor` exists to
// enforce elsewhere: no evidence is not a pass, and here it is not a zero either.
//
// The per-agent line is BEST-EFFORT by construction. It reaches a second endpoint and swallows any
// failure, because the pool table is the answer and the caller's own row is a convenience — a broken
// lookup must not cost the report.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { AIFY_AGENT_ID } from "./launch-identity.mjs";
import { personalQuotaLine } from "./usage-predicates.mjs";

// Registers the usage tool on an MCP server. A function rather than a module-scope side effect, so a fake
// server can capture the registration and a test can call the handler without an MCP transport. `z` is the
// caller's zod — see the other tool groups for why it is not imported here.
//
// The body below is the original server.js text, indented one level. Nothing else changed.
export function registerUsageTool(server, z) {
  server.tool(
    "comms_usage",
    "Show remaining subscription quota per source pool (Anthropic Claude, OpenAI ChatGPT-Codex) and your own. Advisory: a pool near 0% means agents on it should hand work to a pool with headroom.",
    {},
    async () => {
      if (!IS_REMOTE) {
        return { content: [{ type: "text", text: "Usage data requires remote server mode." }], isError: true };
      }
      const r = await httpCall("GET", "/usage");
      const pools = (r && r.pools) || [];
      if (!pools.length) return { content: [{ type: "text", text: "No usage data yet (collector warming up)." }] };
      const fmt = (p) => {
        const w = p.weekly || {};
        const f = p.five_hour || {};
        const left = w.left_pct == null ? "?" : `${w.left_pct}%`;
        const fleft = f.left_pct == null ? "?" : `${f.left_pct}%`;
        const sev = p.severity && p.severity !== "normal" ? ` [${p.severity}]` : "";
        const tags = `${p.stale ? " (stale)" : ""}${p.unknown ? " (unknown)" : ""}`;
        return `- ${p.source_id}: weekly ${left} left, 5h ${fleft} left${sev}${tags}`;
      };
      let mine = "";
      if (AIFY_AGENT_ID) {
        try {
          const info = await httpCall("GET", `/agents/${encodeURIComponent(AIFY_AGENT_ID)}`);
          const a = (info && info.agent) || {};
          mine = personalQuotaLine(AIFY_AGENT_ID, a);
        } catch { /* best-effort */ }
      }
      return { content: [{ type: "text", text: `Quota pools (% remaining):\n${pools.map(fmt).join("\n")}${mine}` }] };
    }
  );
}
