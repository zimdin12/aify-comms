// listening-ports.mjs: the socket table, which names a listener's pid even when its command line cannot be read.

import assert from "node:assert/strict";
import net from "node:net";
import test from "node:test";

import { listListeners, parseLsofListeners, parseNetstatListeners, parseSsListeners } from "../listening-ports.mjs";

test("netstat rows, as captured on 2026-09-15 with an elevated gateway on 9273", () => {
  const text = [
    "",
    "Active Connections",
    "",
    "  Proto  Local Address          Foreign Address        State           PID",
    "  TCP    0.0.0.0:8800           0.0.0.0:0              LISTENING       11744",
    "  TCP    127.0.0.1:9273         0.0.0.0:0              LISTENING       65916",
    "  TCP    127.0.0.1:50123        127.0.0.1:8800         ESTABLISHED     4000",
    "  TCP    [::1]:8811             [::]:0                 LISTENING       12924",
  ].join("\r\n");
  assert.deepEqual(parseNetstatListeners(text), [{ port: 8800, pid: 11744 }, { port: 9273, pid: 65916 }, { port: 8811, pid: 12924 }],
    "an established connection is not a listener, and an IPv6 address still has a port");
});

test("ss and lsof rows, including a socket whose pid this user may not see", () => {
  const ss = [
    'LISTEN 0 511 127.0.0.1:9273 0.0.0.0:* users:(("python3",pid=4321,fd=7))',
    "LISTEN 0 4096 [::]:22 [::]:*",
    "ESTAB 0 0 127.0.0.1:1 127.0.0.1:2",
  ].join("\n");
  assert.deepEqual(parseSsListeners(ss), [{ port: 9273, pid: 4321 }, { port: 22, pid: null }]);
  assert.deepEqual(parseLsofListeners("p501\nf7\nn127.0.0.1:9273\nn*:9274\np502\nn[::1]:8800\n"),
    [{ port: 9273, pid: 501 }, { port: 9274, pid: 501 }, { port: 8800, pid: 502 }]);
});

test("a listing that failed THROWS: a caller deciding a port is free must not be handed an empty answer", () => {
  for (const res of [null, { status: 1, stdout: "" }, { status: null, error: Object.assign(new Error("t"), { code: "ETIMEDOUT" }), stdout: "  TCP    127.0.0.1:9273    0.0.0.0:0    LISTENING    1" }]) {
    assert.throws(() => listListeners({ platform: "win32", run: () => res }), /netstat failed/);
  }
  assert.throws(() => listListeners({ platform: "sunos", run: () => ({ status: 0, stdout: "" }) }), /no listening-socket probe/);
  const twice = "  TCP    0.0.0.0:9273    0.0.0.0:0    LISTENING    7\r\n  TCP    [::]:9273    [::]:0    LISTENING    7\r\n";
  assert.deepEqual(listListeners({ platform: "win32", run: () => ({ status: 0, stdout: twice }) }), [{ port: 9273, pid: 7 }], "one socket per port and pid");
});

test("REAL HOST: a port this test listens on is found, with this process as its owner", async (t) => {
  if (!["win32", "linux", "darwin"].includes(process.platform)) {
    t.skip(`no probe for ${process.platform}`);
    return;
  }
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  try {
    let listeners;
    try {
      listeners = listListeners();
    } catch (error) {
      t.skip(`this host cannot list sockets: ${error.message}`);
      return;
    }
    assert.ok(listeners.some((entry) => entry.port === port && entry.pid === process.pid), `port ${port} owned by ${process.pid} was not listed`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
  // CONTROL, the probe can say NO: the same port once closed.
  assert.ok(!listListeners().some((entry) => entry.port === port && entry.pid === process.pid), "a closed port was still listed");
});
