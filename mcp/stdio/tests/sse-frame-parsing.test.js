#!/usr/bin/env node
// Reassembling hermes api_server's SSE stream into frames.
//
// `parseSseStream` is how every token, tool call and turn boundary from a hermes api_server session
// reaches the bridge. It was named by no test. A parser fed by a NETWORK stream is the definition of
// code whose inputs arrive in shapes nobody chose: a frame split across two TCP reads, a multi-line
// `data:` payload, a keepalive comment, a final frame with no trailing blank line.
//
// EVERY CASE HERE IS A REAL WIRE SHAPE, not an invented one:
//   * two framings, because the server uses both — `event:`-named for session-chat, `data:`-only for
//     /v1/runs where the type is inside the JSON;
//   * chunk boundaries in the middle of a frame, mid-line and mid-JSON, because a reader hands back
//     whatever arrived;
//   * `: keepalive` and `: stream closed` comments, which the server sends to hold the connection;
//   * a stream that ends without the final `\n\n`, which is what a closed connection looks like.
//
// A DROPPED FRAME IS INVISIBLE. The bridge does not know what it was not told: a lost turn-end reads
// as an agent still working, and a lost token is a gap in the transcript nobody can reconstruct.

import assert from "node:assert/strict";
import test from "node:test";

import { parseSseStream, DEFAULT_BASE_URL } from "../hermes-apiserver-client.js";

/** A Response-like object whose body yields the given string chunks, as the real reader does. */
function streamOf(chunks) {
  const encoder = new TextEncoder();
  let i = 0;
  return {
    body: {
      getReader() {
        return {
          async read() {
            if (i >= chunks.length) return { value: undefined, done: true };
            return { value: encoder.encode(chunks[i++]), done: false };
          },
        };
      },
    },
  };
}

async function framesFrom(chunks) {
  const frames = [];
  await parseSseStream(streamOf(chunks), (frame) => frames.push(frame));
  return frames;
}

test("the default base url is the api_server's documented port", () => {
  // Named by no test, and it is what every call falls back to when the caller passes nothing.
  assert.equal(DEFAULT_BASE_URL, "http://127.0.0.1:8642");
});

test("a named-event frame yields its name and parsed data", async () => {
  const frames = await framesFrom(['event: message\ndata: {"text":"hi"}\n\n']);
  assert.deepEqual(frames, [{ event: "message", data: { text: "hi" } }]);
});

test("an event name is read whether or not a space follows the colon", async () => {
  // `event:message` is legal SSE and some servers emit it. The name is sliced at a fixed offset and
  // then trimmed, so the SPACED form survives an off-by-one slice unchanged — only the unspaced
  // form can see that mistake, which is why it is here.
  const spaced = await framesFrom(['event: message\ndata: {"n":1}\n\n']);
  const tight = await framesFrom(['event:message\ndata: {"n":1}\n\n']);
  assert.equal(spaced[0].event, "message");
  assert.equal(tight[0].event, "message", "an unspaced event name was mis-sliced");
});

test("a data-only frame yields a null event and the JSON's own type", async () => {
  // /v1/runs carries the type INSIDE the payload; a parser that required `event:` would drop the
  // entire runs stream.
  const frames = await framesFrom(['data: {"event":"turn_end","runId":"r-1"}\n\n']);
  assert.deepEqual(frames, [{ event: null, data: { event: "turn_end", runId: "r-1" } }]);
});

test("a frame split across chunk boundaries is reassembled", async () => {
  // Mid-line, mid-JSON, and across the frame terminator itself — a reader returns whatever arrived,
  // and the split lands wherever the network put it.
  const frames = await framesFrom(["event: mes", 'sage\ndata: {"te', 'xt":"hi"}\n', "\n"]);
  assert.deepEqual(frames, [{ event: "message", data: { text: "hi" } }]);
});

test("several frames in ONE chunk are all delivered", async () => {
  // FOUR frames in one chunk, and a further chunk behind them. Both numbers are deliberate: the
  // inner loop must DRAIN the buffer, and a version taking one frame per read passed a three-frame
  // fixture by coincidence — two reads plus the end-of-stream flush happened to equal three.
  // Verified by mutation.
  const frames = await framesFrom([
    'data: {"n":1}\n\ndata: {"n":2}\n\ndata: {"n":3}\n\ndata: {"n":4}\n\n',
    'data: {"n":5}\n\n',
  ]);
  assert.deepEqual(frames.map((f) => f.data.n), [1, 2, 3, 4, 5]);
});

test("keepalive and comment lines are ignored, not delivered as frames", async () => {
  // WHAT ENFORCES THIS is the `event:`/`data:` prefix test below it, not the explicit comment skip:
  // a line starting with ":" matches neither prefix, contributes nothing, and the frame is dropped
  // for being empty. Verified by mutation — deleting the comment guard changes no outcome. Recorded
  // rather than trimmed, because the guard states the SSE rule plainly and the next person to touch
  // the prefix tests should know it is the only thing standing here.
  const frames = await framesFrom([
    ": keepalive\n\n",
    ': stream closed\n\ndata: {"n":1}\n\n',
  ]);
  assert.deepEqual(frames, [{ event: null, data: { n: 1 } }],
    "a comment delivered as a frame would reach the transcript as an empty message");
});

test("a multi-line data payload is joined with newlines before parsing", async () => {
  // SSE may split one payload across several `data:` lines; the spec joins them with newlines, and
  // JSON tolerates a newline between tokens. Joining with "" instead would still parse THIS frame
  // and would corrupt any payload whose split lands inside a token — so the shape is pinned here.
  const frames = await framesFrom(['data: {"text":\ndata: "hello"}\n\n']);
  assert.deepEqual(frames, [{ event: null, data: { text: "hello" } }]);
});

test("a payload whose own content spans lines survives as raw text", async () => {
  // Two data lines that do NOT reassemble into JSON. The join is still what carries the line break,
  // and delivering only the first line would truncate the message silently.
  const frames = await framesFrom(["data: line one\ndata: line two\n\n"]);
  assert.deepEqual(frames, [{ event: null, data: { raw: "line one\nline two" } }]);
});

test("exactly ONE leading space after data: is stripped, and no more", async () => {
  // The first space is SSE syntax; a second one is CONTENT. Stripping both would silently reindent
  // every code block a hermes agent emits.
  //
  // Asserted on a non-JSON payload deliberately: `JSON.parse` ignores leading whitespace, so a JSON
  // frame parses identically either way and cannot see the difference. My first version used one
  // and proved nothing — the parser it was meant to test was not the one deciding the outcome.
  const frames = await framesFrom(["data:  two spaces\n\n"]);
  assert.deepEqual(frames, [{ event: null, data: { raw: " two spaces" } }]);
});

test("unparseable data is handed over as raw text rather than dropped", async () => {
  // A frame the bridge cannot parse is still evidence the server said something. Dropping it would
  // lose a turn boundary and leave an agent reading as busy forever.
  const frames = await framesFrom(["data: not json at all\n\n"]);
  assert.deepEqual(frames, [{ event: null, data: { raw: "not json at all" } }]);
});

test("a final frame with no trailing blank line is still delivered", async () => {
  // What a closed connection looks like. The last frame is usually the one that matters — a turn
  // end, or an error explaining why the stream stopped.
  const frames = await framesFrom(['data: {"event":"turn_end"}']);
  assert.deepEqual(frames, [{ event: null, data: { event: "turn_end" } }]);
});

test("a trailing blank buffer at end of stream is not delivered as an empty frame", async () => {
  const frames = await framesFrom(['data: {"n":1}\n\n', "\n\n   \n"]);
  assert.deepEqual(frames.map((f) => f.data.n), [1]);
});

test("an event line with no data yields the name and an empty object", async () => {
  // The server uses bare named events as signals; a parser that required data would drop them.
  const frames = await framesFrom(["event: done\n\n"]);
  assert.deepEqual(frames, [{ event: "done", data: {} }]);
});

test("an empty stream yields nothing and does not hang", async () => {
  assert.deepEqual(await framesFrom([]), []);
  assert.deepEqual(await framesFrom(["", ""]), []);
});
