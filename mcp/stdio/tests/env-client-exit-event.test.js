#!/usr/bin/env node
// The client half of aify-env's exit event.
//
// aify-env now ends an output stream with `event: exit` carrying the code, because without it a
// delegated process that died is indistinguishable from one that is thinking: an open, silent stream
// either way. This is the reader.
//
// IT MATTERS MORE HERE THAN IT LOOKS. TerminalProcessManager drives `_handleExit` off a pty's exit --
// that is what ends a turn, releases the terminal row, and decides whether the heal path runs. A
// delegated terminal with no exit signal would spawn correctly and then never finish anything.
//
// A FRAME IS NOT A LINE. Output payloads are JSON-encoded precisely because a newline inside them
// would otherwise end an event early, so the exit frame has to be recognised by its `event:` line
// rather than by position or by looking for something that parses.

import assert from "node:assert/strict";
import { test } from "node:test";

import { EnvClient } from "../env-client.mjs";

const LF = String.fromCharCode(10);
const FRAME = LF + LF;

/** A fetch whose body yields the frames it was given, then closes. */
function streamingFetch(frames) {
  return async () => ({
    ok: true,
    status: 200,
    body: {
      getReader() {
        const encoder = new TextEncoder();
        let i = 0;
        return {
          async read() {
            if (i >= frames.length) return { done: true, value: undefined };
            return { done: false, value: encoder.encode(frames[i++]) };
          },
        };
      },
    },
  });
}

const client = (fetchImpl) => new EnvClient({ endpoint: "http://127.0.0.2:1", fetchImpl });

test("output frames reach the listener and the exit frame does not", async () => {
  const seen = [];
  const exits = [];
  const frames = [
    `data: ${JSON.stringify("hello")}${FRAME}`,
    `event: exit${LF}data: ${JSON.stringify({ code: 0 })}${FRAME}`,
  ];
  await client(streamingFetch(frames)).subscribeOutput("p1", (c) => seen.push(c), (code) => exits.push(code));
  await new Promise((r) => setTimeout(r, 30));

  assert.deepEqual(seen, ["hello"], "the exit frame was delivered as if it were output");
  assert.deepEqual(exits, [0]);
});

test("a non-zero exit code is carried, not flattened", async () => {
  // The code decides whether a session heals or is reported as failed; losing it loses the decision.
  const exits = [];
  const frames = [`event: exit${LF}data: ${JSON.stringify({ code: 7 })}${FRAME}`];
  await client(streamingFetch(frames)).subscribeOutput("p1", () => {}, (code) => exits.push(code));
  await new Promise((r) => setTimeout(r, 30));
  assert.deepEqual(exits, [7]);
});

test("output containing the word exit is still output", async () => {
  // The discriminator is the `event:` line, not the payload. An agent printing "event: exit" would
  // otherwise end its own terminal.
  const seen = [];
  const exits = [];
  const frames = [`data: ${JSON.stringify(`event: exit${LF}data: {"code":1}`)}${FRAME}`];
  await client(streamingFetch(frames)).subscribeOutput("p1", (c) => seen.push(c), (code) => exits.push(code));
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(seen.length, 1);
  // The stream still ENDS here, and ending is now itself an exit -- so the assertion is not "no exit"
  // but "no exit CODE taken from the payload". A 1 would mean the agent's own text was read as an exit.
  assert.deepEqual(exits, [null], "an agent printing an exit frame was read as an exit");
});

test("subscribing without an exit listener still works", async () => {
  // Every caller before this passed two arguments.
  const seen = [];
  const frames = [`data: ${JSON.stringify("x")}${FRAME}`, `event: exit${LF}data: {"code":0}${FRAME}`];
  const stop = await client(streamingFetch(frames)).subscribeOutput("p1", (c) => seen.push(c));
  await new Promise((r) => setTimeout(r, 30));
  assert.notEqual(stop, null);
  assert.deepEqual(seen, ["x"]);
});

test("a malformed exit payload does not fire a bogus exit", async () => {
  const exits = [];
  const frames = [`event: exit${LF}data: {not json${FRAME}`];
  await client(streamingFetch(frames)).subscribeOutput("p1", () => {}, (code) => exits.push(code));
  await new Promise((r) => setTimeout(r, 30));
  // Same distinction: the malformed frame must not yield a code. The stream then ends, which reports
  // null -- "we stopped being able to see it" rather than "it finished with status 0".
  assert.deepEqual(exits, [null], "an unreadable exit frame produced a code out of nowhere");
});

test("a stream that ENDS without an exit frame still reports an exit", async () => {
  // The aify-env-died case. If the environment goes away, the socket simply closes -- there is no exit
  // frame, because nothing was there to send one. Returning quietly would leave the caller's terminal
  // marked attached forever with no process behind it: a dead agent wearing the shape of a thinking
  // one, which is the failure the exit event exists to prevent, one level up.
  //
  // The code is NULL rather than 0. A clean zero would tell the heal path the agent finished normally,
  // and it did not -- we simply stopped being able to see it.
  const exits = [];
  const frames = [`data: ${JSON.stringify("partial output")}${FRAME}`];
  await client(streamingFetch(frames)).subscribeOutput("p1", () => {}, (code) => exits.push(code));
  await new Promise((r) => setTimeout(r, 40));

  assert.equal(exits.length, 1, "the stream ended and nobody was told");
  assert.equal(exits[0], null, "a lost stream must not read as a clean exit");
});

test("an exit frame still wins over the stream simply ending", async () => {
  // Both paths can fire on the same stream -- the frame arrives, then the socket closes. The caller
  // must be told once, with the real code.
  const exits = [];
  const frames = [`event: exit${LF}data: ${JSON.stringify({ code: 3 })}${FRAME}`];
  await client(streamingFetch(frames)).subscribeOutput("p1", () => {}, (code) => exits.push(code));
  await new Promise((r) => setTimeout(r, 40));
  assert.deepEqual(exits, [3], "a real exit code was lost or reported twice");
});
