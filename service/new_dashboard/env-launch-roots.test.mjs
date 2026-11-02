// env-launch-roots.mjs draws the service's launch-roots verdict; it decides nothing. These call its two exports directly.
// environments-panels.test.mjs keeps the composition (the card carries the chip and the note); what each one says is
// pinned here.

import assert from "node:assert/strict";
import test from "node:test";

import { launchRootsNote, rootChip } from "./env-launch-roots.mjs";

const titleOf = (html) => html.match(/title="([^"]*)"/)?.[1] ?? null;

test("rootChip: a root the service did not list as outside stays plain", () => {
  assert.equal(rootChip({ rootsOutsideLaunch: [{ root: "D:/", fix: "x" }] }, "C:/"), "<code>C:/</code>");
  assert.equal(rootChip({}, "C:/"), "<code>C:/</code>", "no verdict at all is no mark");
});

test("rootChip: an outside root carries the service's own fix sentence, escaped, and invents none", () => {
  const env = { rootsOutsideLaunch: [
    { root: "D:/My Games", add: 'aify-env roots add "D:/My Games"', fix: 'on that host, add it as a launch root: `aify-env roots add "D:/My Games"`' },
    { root: "//server/share", add: "", fix: "`aify-env roots add` refuses it (//server/share: UNC and device paths are not supported yet)" },
    { root: "D:/old", add: "" },
  ] };
  const spaced = rootChip(env, "D:/My Games");
  assert.match(spaced, /class="mb-warn root-outside-launch"/);
  assert.match(spaced, />D:\/My Games ⚠<\/code>$/);
  assert.equal(titleOf(spaced),
    "aify-env on this host does not start work here; on that host, add it as a launch root: `aify-env roots add &quot;D:/My Games&quot;`",
    "the service's sentence did not reach the title intact and escaped");
  assert.match(titleOf(rootChip(env, "//server/share")), /refuses it \(\/\/server\/share: UNC/, "the parser's reason was dropped");
  assert.equal(titleOf(rootChip(env, "D:/old")), "aify-env on this host does not start work here.",
    "a fix was invented where the service gave none");
});

test("launchRootsNote: says where aify-env starts work and how many listed roots fall outside", () => {
  const one = launchRootsNote({ metadata: { launchRoots: ["C:\\"] }, rootsOutsideLaunch: [{ root: "D:/", fix: "x" }] });
  assert.match(one, /aify-env starts work under <code>C:\\<\/code>/);
  assert.match(one, /1 listed root outside them/);
  const two = launchRootsNote({ metadata: { launchRoots: ["C:\\"] }, rootsOutsideLaunch: [{ root: "D:/" }, { root: "E:/" }] });
  assert.match(two, /2 listed roots outside them/);
  assert.doesNotMatch(launchRootsNote({ metadata: { launchRoots: ["C:\\", "D:/"] }, rootsOutsideLaunch: [] }), /outside them/,
    "CONTROL: nothing outside, nothing counted");
});

test("launchRootsNote: an aify-env that reported no launch roots gets no note", () => {
  assert.equal(launchRootsNote({ metadata: {} }), "");
  assert.equal(launchRootsNote({ metadata: { launchRoots: [] }, rootsOutsideLaunch: [{ root: "D:/" }] }), "",
    "a count was shown with no reported roots to be outside of");
});
