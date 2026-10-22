// The S2 container witness counts an owned resource as removed only when Docker answers that THIS name does not exist
// (scripts/sandbox/docker-readback.mjs). A daemon it cannot reach, or a not-found about another name, is unknown, and
// an unknown cleanup fails the run (review E3). The answers below are Docker's own wording for each kind.
import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import { test } from "node:test";

const REPO = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const { classifyReadBack, inspectArgs } = await import(pathToFileURL(path.join(REPO, "scripts", "sandbox", "docker-readback.mjs")).href);

const container = { kind: "container", name: "s2w-ab12-b" };
const image = { kind: "image", name: "s2w-ab12-comms" };
const network = { kind: "network", name: "s2w-ab12" };

test("Docker's not-found for this very name is absent, for each kind", () => {
  assert.equal(classifyReadBack(container, { status: 1, out: "Error response from daemon: No such container: s2w-ab12-b\n" }), "absent");
  assert.equal(classifyReadBack(container, { status: 1, out: "Error: No such object: s2w-ab12-b\n" }), "absent");
  assert.equal(classifyReadBack(image, { status: 1, out: "Error response from daemon: No such image: s2w-ab12-comms:latest\n" }), "absent");
  assert.equal(classifyReadBack(network, { status: 1, out: "Error response from daemon: network s2w-ab12 not found\n" }), "absent");
});

test("anything else is present or unknown, never absent", () => {
  assert.equal(classifyReadBack(container, { status: 0, out: "[{...}]" }), "present");
  assert.equal(classifyReadBack(container, { status: 1, out: "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?\n" }), "unknown");
  assert.equal(classifyReadBack(container, { status: 1, out: "error during connect: this error may indicate that the docker daemon is not running\n" }), "unknown");
  assert.equal(classifyReadBack(container, { status: 1, out: "Error response from daemon: No such container: s2w-ab12-b-other\n" }), "unknown", "a not-found about another name");
  assert.equal(classifyReadBack(container, { status: 1, out: "Error response from daemon: No such container: s2w-ab12\n" }), "unknown", "a not-found about a prefix of the name");
  assert.equal(classifyReadBack(network, { status: 1, out: "Error: not found\n" }), "unknown", "a not-found naming nothing");
  assert.equal(classifyReadBack(image, { status: 124, out: "" }), "unknown", "a timeout");
});

test("each kind is inspected by its own command and its exact name", () => {
  assert.deepEqual(inspectArgs(container), ["container", "inspect", "s2w-ab12-b"]);
  assert.deepEqual(inspectArgs(image), ["image", "inspect", "s2w-ab12-comms"]);
  assert.deepEqual(inspectArgs(network), ["network", "inspect", "s2w-ab12"]);
});
