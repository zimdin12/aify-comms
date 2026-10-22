// A stand-in for the Anthropic API inside the throwaway container, so the fake key meets no real service. It logs
// every request. The remote managed settings route answers 404 (no organization policy), as for an account with
// none. Everything else answers 401.
import { appendFileSync } from "node:fs";
import http from "node:http";

const mode = process.argv[2] ?? "404";
http.createServer((req, res) => {
  appendFileSync("/tmp/fake-api.log", `${new Date().toISOString()} ${req.method} ${req.url}\n`);
  if (req.url.startsWith("/api/claude_code/settings")) {
    if (mode === "empty") { res.writeHead(200, { "content-type": "application/json" }); res.end("{}"); return; }
    res.writeHead(404, { "content-type": "application/json" }); res.end('{"error":"not_found"}'); return;
  }
  res.writeHead(401, { "content-type": "application/json" });
  res.end('{"type":"error","error":{"type":"authentication_error","message":"fake"}}');
}).listen(9999, "127.0.0.1");
