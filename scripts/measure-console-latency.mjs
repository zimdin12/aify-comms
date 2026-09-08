#!/usr/bin/env node
// How much LATER the browser's console feed delivers a byte than aify-env's own stream does. READ-ONLY.
//
// WHY THIS EXISTS. The operator reported that the browser terminal "kind of lags sometimes". A first
// attempt compared two independent freshness signals -- when aify-env last produced anything, against
// when the service's sequence last advanced -- and concluded the ingest leg was clear. That
// conclusion was RETRACTED: a continuously producing agent always has a fresh newest byte no matter
// how far behind the consumer is.
//
// ── WHAT IT MEASURES, AND THE LABEL THAT WAS WRONG THE FIRST TIME.
//
// This process opens two streams for ONE process and timestamps each arrival WITH ITS OWN CLOCK:
//
//   aify-env    GET /processes/:id/output   -- an SSE frame arrives HERE
//   aify-comms  WS  /ws  `terminal_output`  -- a console frame arrives HERE
//
// Both timestamps are observations made in this process. NEITHER OF THEM IS THE MOMENT THE PTY
// EMITTED THE BYTE, and an earlier version of this file reported the difference as "PTY-to-delivery
// latency" -- which claimed a clock it never read. Review caught it, and the retraction matters
// because the wrong noun makes the number sound like a bound on the whole path when it bounds one
// leg relative to another.
//
// WHAT THE DIFFERENCE HONESTLY IS: the EXTRA delay on the aify-comms WebSocket path, relative to
// reading aify-env's own stream from this same process at this same moment. The two paths share
// their origin -- aify-env holds the PTY and feeds both -- so whatever happens upstream of that fork
// cancels, and what remains is the service leg: ingest, store, and the push to a subscriber.
//
// WHAT IT THEREFORE CANNOT SAY, and must not be read as saying: how long a byte took to leave the
// PTY, how long the browser then took to paint it, or that the whole path is fast. It says the
// service leg adds this much over the shortest local path, measured here.
//
// IT TAKES NO ACTION. Reading an output stream is what a console does, and adds a subscriber the
// daemon already supports; the WebSocket is a passive listener. Nothing is started, stopped,
// attached, written to or configured, and it exits on its own.
//
// ── THE ALIGNMENT IS THE PART TO DISTRUST, AND IT NEEDED TWO REPAIRS.
//
// The consumer's stream is joined mid-flight, so its first frames describe bytes emitted before this
// started, and the run has to find where one buffer sits inside the other.
//
//   A PROBE THAT APPEARS TWICE IS NOT AN ANCHOR. `indexOf` returns the FIRST match, which for
//   repeated output -- a spinner, a progress bar, a retry loop, exactly what an agent prints -- is
//   the wrong one. Review's counterexample: a true offset of 0 and a true lag of 50ms were reported
//   as an offset of -300 and a lag of 350ms, with 300 confident pairs behind it. The probe must be
//   UNIQUE in the producer buffer, and this refuses when no unique anchor can be found.
//
//   A MISMATCH IS EVIDENCE, NOT NOISE. The old loop `continue`d past any byte that disagreed, so a
//   run could drop 200 mismatches and report 2,300 pairs at a confident 50ms. A mismatch means the
//   alignment is wrong or the streams diverged; either way the number that follows is not about the
//   bytes it claims. They are counted, and enough of them refuses the run.
//
// UNITS ARE UTF-16 CODE UNITS, not bytes. Both streams are decoded to strings before they are
// compared, so every count here is in the unit JavaScript indexes with. For ASCII output the two are
// the same and for anything else they are not, which is why the word "byte" is not used for a count.

import { alignment, distribution, pairLags } from "./console-latency-pairing.mjs";

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

function refuse(why, detail = "") {
  console.log(`\nUNKNOWN: ${why}`);
  if (detail) console.log(detail);
  process.exit(1);
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
    // BUSIEST FIRST: a quiet process produces nothing to follow, and a run that measured nothing
    // must not be mistaken for a run that measured zero.
    .sort((a, b) => (b.lastOutputAtMs || 0) - (a.lastOutputAtMs || 0));
  if (!candidates.length) die(AGENT ? `no terminal-backed process named ${AGENT}` : "no process both sides know");
  const chosen = candidates[0];
  return { process: chosen, terminal: byAgent.get(String(chosen.label)) };
}

/** Read aify-env's SSE, appending decoded output to `sink` with the arrival time of each frame. */
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
  console.log(`aify-env: ${ENV_URL}   aify-comms: ${SERVICE}   for ${SECONDS}s\n`);

  //: index -> the moment THIS PROCESS received the unit at that index over aify-env's SSE.
  const sseAt = [];
  let produced = "";
  //: index -> the moment THIS PROCESS received the unit at that index over the service's WebSocket.
  const wsAt = [];
  let delivered = "";

  const controller = new AbortController();
  followProducer(subject.id, (text, at) => {
    for (let i = 0; i < text.length; i += 1) sseAt.push(at);
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
    for (let i = 0; i < text.length; i += 1) wsAt.push(at);
    delivered += text;
  });
  socket.addEventListener("error", () => die("the websocket refused the connection (is AIFY_API_KEY set?)"));

  await new Promise((r) => setTimeout(r, SECONDS * 1000));
  controller.abort();
  try { socket.close(); } catch { /* already gone */ }

  console.log(`aify-env stream: ${produced.length} code units   service stream: ${delivered.length} code units`);
  if (produced.length < 200 || delivered.length < 200) {
    refuse("too little traffic to align the two streams. Not a measurement of zero.");
  }

  // ALIGNING AND PAIRING LIVE IN `console-latency-pairing.mjs`, WHERE A TEST CAN REACH THEM. Nothing
  // in this file can be exercised without a live daemon and a live service, which is exactly how two
  // defects in that arithmetic survived here: an independent review found both by copying the logic
  // into throwaway scripts, and a defect fixed in a copy has nothing stopping it coming back.
  const anchor = alignment(produced, delivered);
  if (anchor.problem) {
    refuse(`${anchor.problem}. No latency reported.`,
      "A probe that appears twice is not an anchor: `indexOf` returns the first match, and for\n"
      + "repeated agent output that is the wrong one. A number from a guessed offset is worse than none.");
  }
  console.log(`aligned on a unique ${anchor.probeSize}-unit anchor: consumer unit 0 is producer unit ${anchor.offset}\n`);

  const paired = pairLags({
    produced, delivered, producedAt: sseAt, deliveredAt: wsAt, offset: anchor.offset,
  });
  if (paired.problem) {
    refuse(`${paired.problem}.`,
      "That is either a wrong alignment or two different streams. Either way the timings below\n"
      + "would not be about the units they name, so none are reported.");
  }
  const stats = distribution(paired.lags);
  if (stats.problem) refuse(`${stats.problem}.`);

  console.log(`paired units: ${stats.count}   mismatched: ${paired.mismatched}   outside the overlap: ${paired.unpairable}`);
  console.log("clock A: this process's receipt of an aify-env SSE `data` frame");
  console.log("clock B: this process's receipt of an aify-comms WS `terminal_output` frame");
  console.log("reported: B - A, per unit — the EXTRA delay the service leg adds over reading aify-env");
  console.log("          directly from here. NOT the time since the PTY emitted the unit, and NOT a");
  console.log("          bound on what the browser then takes to paint it.");
  const ms = (value) => `${value.toFixed(1)}ms`;
  console.log(`\n  min ${ms(stats.min)}   p50 ${ms(stats.p50)}   p90 ${ms(stats.p90)}   p99 ${ms(stats.p99)}   max ${ms(stats.max)}`);
}

main().catch((error) => die(error?.message || String(error)));
