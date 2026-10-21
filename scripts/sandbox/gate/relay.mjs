// The gate's relay: aify-env's only path to comms in a pair. Everything is forwarded unchanged to the real comms, except
// that while /tmp/relay/silence exists, `POST /api/v1/agent-state` is answered 503 and never forwarded. That is
// PUBLICATION-ONLY silence: aify-env's claimer heartbeat, its spawn claims, definitions and lifecycle traffic keep
// flowing, so its environment stays in contact and sends still create runs (review R4).
//
// It is also the gate's record of CONTACT (review R5), kept apart from comms' DATA rows: every agent-state body it sees
// is logged with the interval it announced (`reportEveryMs`, null when absent) and what became of it: refused here,
// or forwarded and answered by comms (204 is applied, which is what renews comms' report pair).
//
//   node relay.mjs        listens on 8800, forwards to http://comms-real:8800, logs to /tmp/relay/log.jsonl

import fs from "node:fs";
import http from "node:http";

const UPSTREAM = { host: process.env.RELAY_UPSTREAM || "comms-real", port: 8800 };
const SILENCE = "/tmp/relay/silence";
const LOG = "/tmp/relay/log.jsonl";
const PUBLISH = "/api/v1/agent-state";
fs.mkdirSync("/tmp/relay", { recursive: true });

const log = (entry) => fs.appendFileSync(LOG, `${JSON.stringify({ t: Date.now(), ...entry })}\n`);

/** What the gate needs from a publication, never its rows: whose, which, and the interval it announced. */
function describe(raw) {
  try {
    const body = JSON.parse(raw);
    return { kind: body.kind ?? null, instance: body.instance ?? null, publication: body.publication ?? null,
      generation: body.generation ?? null, reportEveryMs: Number.isInteger(body.reportEveryMs) ? body.reportEveryMs : null };
  } catch { return { unparsed: true }; }
}

function forward(req, res, body) {
  const upstream = http.request({ ...UPSTREAM, method: req.method, path: req.url, headers: req.headers }, (answer) => {
    res.writeHead(answer.statusCode, answer.headers);
    answer.pipe(res);
    if (body !== null) log({ ...describe(body), forwarded: true, status: answer.statusCode });
  });
  upstream.on("error", (error) => {
    if (body !== null) log({ ...describe(body), forwarded: true, status: 0, error: String(error.message) });
    res.writeHead(502).end();
  });
  if (body === null) req.pipe(upstream);
  else upstream.end(body);
}

http.createServer((req, res) => {
  const publication = req.method === "POST" && req.url.split("?")[0] === PUBLISH;
  if (!publication) return forward(req, res, null);
  const chunks = [];
  req.on("data", (chunk) => chunks.push(chunk));
  req.on("end", () => {
    const body = Buffer.concat(chunks).toString("utf8");
    if (fs.existsSync(SILENCE)) {
      log({ ...describe(body), forwarded: false, status: 503 });
      res.writeHead(503, { "content-type": "application/json" }).end('{"silenced":true}');
      return;
    }
    forward(req, res, body);
  });
}).listen(8800, () => log({ event: "listening", upstream: UPSTREAM.host }));
