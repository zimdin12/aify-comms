#!/usr/bin/env node
// How long a byte takes to get from a PTY to the browser's console feed. READ-ONLY.
//
// WHY THIS EXISTS. The operator reported that the browser terminal "kind of lags sometimes". A first
// attempt to measure it compared two independent freshness signals -- when aify-env last produced
// anything, against when the service's sequence last advanced -- and concluded the ingest leg was
// clear. That conclusion was RETRACTED: a continuously producing agent always has a fresh newest byte
// no matter how far behind the consumer is, so the two observations need not name the same bytes, and
// sampling only at advances omits exactly the intervals where a stall would live.
//
// THIS FOLLOWS THE BYTES THEMSELVES. Two streams are read at once for ONE process:
//
//   aify-env    GET /processes/:id/output   -- what the PTY emitted, at the producer
//   aify-comms  WS  /ws  `terminal_output`  -- what a browser console is actually delivered
//
// Both are appended to running buffers. For each byte in the producer's stream, delivery is the
// arrival of the first consumer frame whose accumulated payload covers that byte's index. The
// difference is that byte's latency. Byte-to-event correspondence, not two freshness clocks.
//
// IT TAKES NO ACTION. Reading an output stream is what a console does, and adds a subscriber the
// daemon already supports; the WebSocket is a passive listener. Nothing is started, stopped,
// attached, written to or configured, and it exits on its own.
//
// WHAT IT CANNOT SEE, and must not be read as covering: the browser's own xterm write and render.
// This measures PTY-to-delivery, which is every leg the two servers own.
//
// THE ALIGNMENT IS THE PART TO DISTRUST. The consumer's stream is joined mid-flight, so its first
// frames describe bytes the producer emitted before this started. The run therefore SYNCHRONISES
// first -- it finds where the consumer's buffer sits inside the producer's -- and reports how it did
// so. A run that never synchronises reports UNKNOWN and no numbers, because a latency computed from
// a guessed offset is worse than no latency at all.

const args = new Map(process.argv.slice(2).map((a) => {
  const [k, ...rest] = a.replace(/^--/, "").split("=");
  return [k, rest.join("=") || "true"];
}));

const SERVICE = args.get("service") || "http://127.0.0.1:8800";
const ENV_URL = args.get("env") || "http://127.0.0.1:8802";
const SECONDS = Number(args.get("seconds") || 20);
const AGENT = args.get("agent") || "";
const KEY = process.env.AIFY_API_KEY || process.env.CLAUDE_MCP_API_KEY || "";

const HEADERS = KEY ? { "X-API-Key": KEY } : {};
const now = () => Number(process.hrtime.bigint() / 1000n) / 1000;   // ms, monotonic

function die(why) {
  console.error(`cannot measure: ${why}`);
  process.exit(2);
}

/** The busiest terminal-backed process both sides agree on, or the one named. */
async function pickSubject() {
  const health = await fetch(`${ENV_URL}/health`).then((r) => r.json()).catch(() => null);
  if (!health) die(`no answer from aify-env at ${ENV_URL}`);
  const terms = await fetch(`${SERVICE}/api/v1/terminals?limit=50`, { headers: HEADERS })
    .then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!terms) die(`no answer from aify-comms at ${SERVICE} (is AIFY_API_KEY set?)`);

  const rows = Array.isArray(terms.terminals) ? terms.terminals : Object.values(terms.terminals || {});
  const byAgent = new Map(rows.map((t) => [String(t.agentId), t]));
  const candidates = (health.processes || [])
    .filter((p) => p.terminal && byAgent.has(String(p.label)))
    .filter((p) => !AGENT || String(p.label) === AGENT)
    // BUSIEST FIRST: a quiet process produces no bytes to follow, and a run that measures nothing
    // must not be mistaken for a run that measured zero.
    .sort((a, b) => (b.lastOutputAtMs || 0) - (a.lastOutputAtMs || 0));
  if (!candidates.length) die(AGENT ? `no terminal-backed process named ${AGENT}` : "no process both sides know");
  const chosen = candidates[0];
  return { process: chosen, terminal: byAgent.get(String(chosen.label)) };
}

/** Read the producer's SSE, appending decoded output to `buffer` with a timestamp per chunk. */
async function followProducer(id, sink, signal) {
  const response = await fetch(`${ENV_URL}/processes/${encodeURIComponent(id)}/output`,
    { signal, redirect: "manual" });
  if (!response.ok || !response.body) die(`aify-env refused the output stream (HTTP ${response.status})`);
  const decoder = new TextDecoder();
  let carry = "";
  for await (const bytes of response.body) {
    carry += decoder.decode(bytes, { stream: true });
    let end = carry.indexOf("\n\n");
    while (end !== -1) {
      const frame = carry.slice(0, end);
      carry = carry.slice(end + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      // NAMED FRAMES ARE SKIPPED: `meta` and `exit` describe the stream rather than being part of it.
      if (line && !frame.startsWith("event:")) {
        try { sink(JSON.parse(line.slice(5).replace(/^ /, "")), now()); } catch { /* not our frame */ }
      }
      end = carry.indexOf("\n\n");
    }
  }
}

async function main() {
  const { process: subject, terminal } = await pickSubject();
  console.log(`following ${subject.label} (${subject.id}) -> terminal ${terminal.id}`);
  console.log(`producer: ${ENV_URL}   consumer: ${SERVICE}   for ${SECONDS}s\n`);

  //: index -> the moment the producer emitted the byte at that index
  const producedAt = [];
  let produced = "";
  //: index -> the moment the consumer delivered the byte at that index
  const deliveredAt = [];
  let delivered = "";

  const controller = new AbortController();
  followProducer(subject.id, (text, at) => {
    for (let i = 0; i < text.length; i += 1) producedAt.push(at);
    produced += text;
  }, controller.signal).catch(() => {});

  const wsUrl = `${SERVICE.replace(/^http/, "ws")}/ws${KEY ? `?api_key=${encodeURIComponent(KEY)}` : ""}`;
  const socket = new WebSocket(wsUrl);
  socket.addEventListener("message", (event) => {
    let frame;
    try { frame = JSON.parse(String(event.data)); } catch { return; }
    if (frame?.event !== "terminal_output" && frame?.type !== "terminal_output") return;
    const data = frame.data ?? frame;
    if (String(data.terminalId) !== String(terminal.id)) return;
    const text = String(data.output ?? "");
    const at = now();
    for (let i = 0; i < text.length; i += 1) deliveredAt.push(at);
    delivered += text;
  });
  socket.addEventListener("error", () => die("the websocket refused the connection (is AIFY_API_KEY set?)"));

  await new Promise((r) => setTimeout(r, SECONDS * 1000));
  controller.abort();
  try { socket.close(); } catch { /* already gone */ }

  console.log(`producer bytes: ${produced.length}   consumer bytes: ${delivered.length}`);
  if (produced.length < 200 || delivered.length < 200) {
    console.log("\nUNKNOWN: too little traffic to align the two streams. Not a measurement of zero.");
    process.exit(1);
  }

  // ALIGN. The consumer stream was joined mid-flight, so find where its buffer starts inside the
  // producer's. A distinctive slice from the consumer is searched for in the producer; if it is not
  // found, the two are not describing the same bytes and no number here would mean anything.
  const probe = delivered.slice(Math.floor(delivered.length / 2), Math.floor(delivered.length / 2) + 120);
  const at = produced.indexOf(probe);
  if (probe.length < 40 || at === -1) {
    console.log("\nUNKNOWN: could not align the two streams on shared bytes. No latency reported.");
    console.log("A number computed from a guessed offset is worse than no number.");
    process.exit(1);
  }
  const offset = at - Math.floor(delivered.length / 2);
  console.log(`aligned: consumer byte 0 is producer byte ${offset}\n`);

  const lags = [];
  for (let i = Math.max(0, -offset); i < delivered.length; i += 1) {
    const p = i + offset;
    if (p < 0 || p >= producedAt.length) continue;
    if (delivered[i] !== produced[p]) continue;      // drifted; stop trusting this pairing
    lags.push(deliveredAt[i] - producedAt[p]);
  }
  if (lags.length < 100) {
    console.log("UNKNOWN: too few paired bytes to report a distribution.");
    process.exit(1);
  }
  lags.sort((a, b) => a - b);
  const at_ = (q) => lags[Math.min(lags.length - 1, Math.floor(lags.length * q))].toFixed(1);
  console.log(`paired bytes: ${lags.length}`);
  console.log("field: aify-env /processes/:id/output SSE data frames  -- noun: when the PTY emitted this byte");
  console.log("field: aify-comms WS terminal_output.output            -- noun: when a console was delivered it");
  console.log(`\n  min ${at_(0)}ms   p50 ${at_(0.5)}ms   p90 ${at_(0.9)}ms   p99 ${at_(0.99)}ms   max ${lags.at(-1).toFixed(1)}ms`);
}

main().catch((error) => die(error?.message || String(error)));
