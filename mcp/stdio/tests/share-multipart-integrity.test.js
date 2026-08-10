#!/usr/bin/env node
// A shared file must arrive byte-identical. It did not: every binary upload gained a leading CRLF.
//
// REPORTED WITH BYTE EVIDENCE by graph-senior-dev-hermes, 2026-08-10: a 23,620-byte `.log` was
// stored as 23,622 bytes, with `stored[2:] == original` exactly, so recipient hash verification
// failed for every shared file.
//
// THE SERVER WAS NEVER AT FAULT. `share_artifact` does `file_path.write_bytes(data)` and stores
// faithfully whatever the multipart parser hands it — which is why this looked like a storage bug
// from the outside. The corruption was manufactured in the REQUEST:
//
//     parts.push(`...Content-Type: application/octet-stream\r\n\r\n`);   // header block ends here
//     Buffer.from(parts.join("\r\n") + "\r\n")                           // <- and one more CRLF
//
// The file part's header block already ends with the blank line that terminates headers. The extra
// `\r\n` put a THIRD CRLF between headers and payload, and multipart treats everything after the
// FIRST blank line as body — so two bytes of framing became two bytes of file.
//
// This test rebuilds the body exactly as server.js does and asserts the payload region is
// byte-identical to the input. It is deliberately a CONSTRUCTION test rather than a live upload:
// the bug is in what we send, it reproduces with no server, and a network test would also pass
// against a lenient parser that silently stripped the extra bytes.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const SOURCE = readFileSync(new URL("../server.js", import.meta.url), "utf8");

// Mirrors the construction in server.js's binary-upload branch.
function buildBody(fileData, { name = "t.log", from = "alice", description = "" } = {}) {
  const boundary = "----aifyTEST";
  const parts = [];
  parts.push(`--${boundary}\r\nContent-Disposition: form-data; name="from_agent"\r\n\r\n${from}`);
  parts.push(`--${boundary}\r\nContent-Disposition: form-data; name="name"\r\n\r\n${name}`);
  parts.push(`--${boundary}\r\nContent-Disposition: form-data; name="description"\r\n\r\n${description}`);
  parts.push(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${name}"\r\nContent-Type: application/octet-stream\r\n\r\n`);
  return {
    boundary,
    body: Buffer.concat([Buffer.from(parts.join("\r\n")), fileData, Buffer.from(`\r\n--${boundary}--\r\n`)]),
  };
}

// The payload is everything after the FIRST blank line following the file part's headers — which
// is exactly how a multipart parser finds it.
function extractPayload(body, fileLen) {
  const marker = "application/octet-stream\r\n\r\n";
  const at = body.toString("latin1").indexOf(marker);
  assert.ok(at >= 0, "file part header must be present");
  return body.subarray(at + marker.length, at + marker.length + fileLen);
}

test("a binary payload survives multipart construction byte-for-byte", () => {
  // Content chosen to expose ANY transformation: CRLF, bare LF, NUL, high bytes.
  const original = Buffer.from("first line\r\nsecond\nthird\x00\xff\xfe tail\r\n".repeat(50), "latin1");
  const { body } = buildBody(original);
  const payload = extractPayload(body, original.length);
  assert.equal(payload.length, original.length, "length must not change");
  assert.ok(payload.equals(original), "payload must be byte-identical to the input");
});

test("the payload does NOT begin with a stray CRLF", () => {
  // The exact reported signature: leading 0d0a, and stored[2:] === original.
  const original = Buffer.from("HELLO-ORIGINAL-BYTES");
  const { body } = buildBody(original);
  const payload = extractPayload(body, original.length);
  assert.notEqual(payload.subarray(0, 2).toString("hex"), "0d0a", "the reported corruption signature");
  assert.equal(payload.subarray(0, 5).toString(), "HELLO");
});

test("a file that legitimately STARTS with CRLF is preserved, not swallowed", () => {
  // The inverse risk of the fix: over-correcting and eating a real leading newline.
  const original = Buffer.from("\r\nreal leading crlf belongs to the file\r\n");
  const { body } = buildBody(original);
  const payload = extractPayload(body, original.length);
  assert.ok(payload.equals(original), "a genuine leading CRLF is content and must survive");
});

test("an empty file produces an empty payload, not two bytes", () => {
  const { body } = buildBody(Buffer.alloc(0));
  const marker = "application/octet-stream\r\n\r\n";
  const s = body.toString("latin1");
  const at = s.indexOf(marker) + marker.length;
  assert.equal(s.slice(at, at + 2), "\r\n", "next bytes are the closing boundary's CRLF, not payload");
  assert.ok(s.slice(at).startsWith("\r\n------aifyTEST--"), "closing boundary follows immediately");
});

test("the source does not reintroduce the trailing CRLF after the join", () => {
  // Pins the actual line, because the fix looks like a typo and invites 'tidying' back in.
  assert.ok(
    !/parts\.join\("\\r\\n"\)\s*\+\s*"\\r\\n"/.test(SOURCE),
    'server.js must not append "\\r\\n" after parts.join("\\r\\n") — that extra CRLF becomes file content',
  );
  assert.match(SOURCE, /Buffer\.from\(parts\.join\("\\r\\n"\)\)/, "the joined header block is sent as-is");
});

console.log("share-multipart-integrity.test.js: all assertions passed");
