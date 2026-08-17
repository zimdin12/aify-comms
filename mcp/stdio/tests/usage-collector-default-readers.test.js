// The usage collector's DEFAULT readers, against a sealed fake home.
//
// Fifteenth cluster off the V8-coverage census: `defaultReadCreds`, `defaultReadLatestRollout`,
// `defaultFetchCodex` and the `readFile` default inside `readAgentConsumption` all had a zero call count -
// the same shape as `defaultKillTree` two slices ago. Every existing test injects its own reader, so the call
// sites look thoroughly covered while the wiring behind the default parameter has never read a byte.
//
// WHY THAT MATTERS HERE. These four are how the operator's quota numbers reach the dashboard. A default that
// looks in the wrong place does not error - it returns nothing, and the fleet reads `unknown` for a source
// whose usage is sitting on disk. That failure is indistinguishable from "no data yet".
//
// SEALED, AND THE SEAL IS ASSERTED. Every reader resolves its paths from HOME/USERPROFILE plus CODEX_HOME,
// HERMES_HOME, LOCALAPPDATA and XDG_CONFIG_HOME, at CALL time. All six are pointed inside a temp dir, and
// `openAiAuthCandidates()` is checked to contain nothing outside it - so no test here can read the operator's
// real credential stores. (A test of mine did read a live hermes marker once; that is why the seal is
// asserted rather than assumed.) Every token in this file is a fixture string, and none is printed.
//
// NO NETWORK. `fetchImpl` is injected wherever a token exists; where it does not, the code returns before it
// would fetch, which is asserted by leaving `fetchImpl` out and requiring the call to still settle.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  fetchAnthropicUsage,
  fetchCodexUsage,
  collectOnce,
  openAiAuthCandidates,
  readAgentConsumption,
} from "../usage-collector.js";

const SEALED_KEYS = ["HOME", "USERPROFILE", "CODEX_HOME", "HERMES_HOME", "LOCALAPPDATA", "XDG_CONFIG_HOME"];

async function withSealedHome(run) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aify-usage-home-"));
  const saved = new Map(SEALED_KEYS.map((key) => [key, process.env[key]]));
  process.env.HOME = root;
  process.env.USERPROFILE = root;
  process.env.CODEX_HOME = path.join(root, "codex-home");
  process.env.HERMES_HOME = path.join(root, "hermes-home");
  process.env.LOCALAPPDATA = path.join(root, "LocalAppData");
  process.env.XDG_CONFIG_HOME = path.join(root, "xdg");

  // THE SEAL, CHECKED. Every auth-store candidate must live inside the sandbox; one escapee means this test
  // is reading the operator's real store.
  for (const candidate of openAiAuthCandidates()) {
    assert.ok(candidate.startsWith(root), `an auth candidate escaped the sandbox: ${candidate}`);
  }

  try {
    return await run(root);
  } finally {
    for (const [key, value] of saved) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    fs.rmSync(root, { recursive: true, force: true });
  }
}

const writeFile = (file, text) => {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text, "utf8");
};

const rolloutLine = (fivePct, weekPct) => `${JSON.stringify({
  type: "response",
  payload: { rate_limits: { primary: { used_percent: fivePct }, secondary: { used_percent: weekPct } } },
})}\n`;

// ── defaultReadCreds ────────────────────────────────────────────────────────

test("the anthropic reader finds the credentials where claude actually writes them", async () => {
  // `~/.claude/.credentials.json`. Reached only as fetchAnthropicUsage's default parameter, so this is the
  // only way to establish that the path is right.
  await withSealedHome(async (root) => {
    writeFile(path.join(root, ".claude", ".credentials.json"),
      JSON.stringify({ claudeAiOauth: { accessToken: "fixture-not-a-real-token" } }));

    const requests = [];
    const usage = await fetchAnthropicUsage({
      fetchImpl: async (url, init) => {
        requests.push({ url, authorization: init?.headers?.authorization });
        return {
          ok: true,
          json: async () => ({
            five_hour: { utilization: 12, resets_at: "2026-08-17T12:00:00Z" },
            seven_day: { utilization: 34, resets_at: "2026-08-24T12:00:00Z" },
          }),
        };
      },
    });

    assert.equal(requests.length, 1, "the stored token was never used to ask for usage");
    assert.equal(requests[0].authorization, "Bearer fixture-not-a-real-token",
      "the token from the credentials file did not reach the request");
    assert.notEqual(usage.unknown, true, "usage came back unknown despite readable credentials");
  });
});

test("no credentials file means UNKNOWN, not a crash", async () => {
  // The default reader throws (readFileSync on a missing path); fetchAnthropicUsage's own try/catch is what
  // turns that into `unknown`, which is what the dashboard renders as "no data".
  await withSealedHome(async () => {
    const usage = await fetchAnthropicUsage({ fetchImpl: async () => { throw new Error("must not fetch"); } });
    assert.equal(usage.unknown, true);
  });
});

test("a credentials file with no oauth token asks for nothing", async () => {
  await withSealedHome(async (root) => {
    writeFile(path.join(root, ".claude", ".credentials.json"), JSON.stringify({ somethingElse: true }));
    let fetched = false;
    const usage = await fetchAnthropicUsage({ fetchImpl: async () => { fetched = true; return { ok: false }; } });
    assert.equal(fetched, false, "a request was sent with no token");
    assert.equal(usage.unknown, true);
  });
});

// ── the readFile default in readAgentConsumption ────────────────────────────

test("per-agent consumption reads the claude transcript from its deterministic path", async () => {
  // The path is derived from cwd + sessionHandle with every non-alphanumeric character replaced by a dash.
  // Nothing had ever exercised the default reader against a real file, so nothing established that the
  // encoding this builds matches where claude writes.
  await withSealedHome(async (root) => {
    const cwd = "C:/work/my project";
    const sessionHandle = "sess-abc123";
    const encoded = cwd.replace(/[^a-zA-Z0-9]/g, "-");
    writeFile(path.join(root, ".claude", "projects", encoded, `${sessionHandle}.jsonl`), [
      JSON.stringify({ type: "assistant", message: { usage: { input_tokens: 10, output_tokens: 5 } } }),
      JSON.stringify({ type: "assistant", message: { usage: { input_tokens: 3, output_tokens: 1 } } }),
    ].join("\n"));

    const consumption = readAgentConsumption({ runtime: "claude-code", cwd, sessionHandle });
    assert.ok(consumption, "the transcript on disk was not found by the default reader");
    assert.equal(consumption.input_tokens, 13);
    assert.equal(consumption.output_tokens, 6);
  });
});

test("a missing transcript is null rather than an exception", async () => {
  await withSealedHome(async () => {
    assert.equal(readAgentConsumption({
      runtime: "claude-code", cwd: "C:/nope", sessionHandle: "sess-missing",
    }), null);
  });
});

// ── defaultReadLatestRollout ────────────────────────────────────────────────

test("the codex reader finds a rollout nested under the codex home's sessions dir", async () => {
  await withSealedHome(async () => {
    writeFile(path.join(process.env.CODEX_HOME, "sessions", "2026", "08", "rollout-a.jsonl"),
      rolloutLine(41, 62));
    const usage = await fetchCodexUsage();
    assert.notEqual(usage.unknown, true, "a readable rollout still reported unknown");
    assert.equal(usage.five_hour.used_pct, 41);
  });
});

test("the NEWEST rollout wins, across directories", async () => {
  // Quota is a point-in-time reading; an older snapshot is a wrong number, not a stale-but-harmless one.
  await withSealedHome(async () => {
    const older = path.join(process.env.CODEX_HOME, "sessions", "old", "rollout-old.jsonl");
    const newer = path.join(process.env.CODEX_HOME, "sessions", "new", "deeper", "rollout-new.jsonl");
    writeFile(older, rolloutLine(11, 12));
    writeFile(newer, rolloutLine(88, 91));
    // Make the ordering explicit rather than relying on write order.
    const past = new Date(Date.now() - 60 * 60 * 1000);
    fs.utimesSync(older, past, past);

    const usage = await fetchCodexUsage();
    assert.equal(usage.five_hour.used_pct, 88, "an older rollout was preferred");
  });
});

test("only rollout-*.jsonl files count", async () => {
  await withSealedHome(async () => {
    const sessions = path.join(process.env.CODEX_HOME, "sessions");
    writeFile(path.join(sessions, "rollout-real.jsonl"), rolloutLine(20, 30));
    // Same directory, newer, and full of rate_limits — but not a rollout file.
    writeFile(path.join(sessions, "notes.jsonl"), rolloutLine(99, 99));
    writeFile(path.join(sessions, "rollout-wrong.txt"), rolloutLine(98, 98));

    const usage = await fetchCodexUsage();
    assert.equal(usage.five_hour.used_pct, 20,
      "a file that is not a codex rollout was read as one");
  });
});

test("a rollout-shaped file OUTSIDE the sessions directory is not a session snapshot", async () => {
  // The walk starts at `<codexHome>/sessions`, not at the codex home. Widening it would pick up anything
  // rollout-named that happens to sit in the codex home - a copied file, an archive, an export - and quota is
  // a point-in-time reading, so the wrong file is a wrong number rather than a missing one.
  await withSealedHome(async () => {
    const real = path.join(process.env.CODEX_HOME, "sessions", "rollout-real.jsonl");
    writeFile(real, rolloutLine(20, 30));
    // Newer, and outside `sessions`.
    writeFile(path.join(process.env.CODEX_HOME, "rollout-decoy.jsonl"), rolloutLine(97, 98));
    writeFile(path.join(process.env.CODEX_HOME, "archive", "rollout-old-copy.jsonl"), rolloutLine(96, 96));
    const past = new Date(Date.now() - 60 * 60 * 1000);
    fs.utimesSync(real, past, past);

    assert.equal((await fetchCodexUsage()).five_hour.used_pct, 20,
      "a rollout outside the sessions directory was read as the live snapshot");
  });
});

test("no rollout anywhere is UNKNOWN", async () => {
  await withSealedHome(async () => {
    assert.equal((await fetchCodexUsage()).unknown, true);
  });
});

test("a codex home that does not exist is skipped, not thrown", async () => {
  // Every home candidate is walked, and most of them will not exist on any given machine.
  await withSealedHome(async () => {
    await assert.doesNotReject(() => fetchCodexUsage());
  });
});

test("only the TAIL of a huge rollout is read — pinned, including what that costs", async () => {
  // The reader takes the last 256KB, which is why a long-running session does not cost a full-file read on
  // every poll. The consequence is real and worth pinning: a rate_limits line that has scrolled out of that
  // window is invisible, and the source reports unknown even though the file contains an answer.
  await withSealedHome(async () => {
    const file = path.join(process.env.CODEX_HOME, "sessions", "rollout-big.jsonl");
    const filler = `${JSON.stringify({ type: "noise", payload: { text: "x".repeat(512) } })}\n`;

    // Case 1: the reading is at the END. Found.
    writeFile(file, filler.repeat(1200) + rolloutLine(77, 79));
    assert.ok(fs.statSync(file).size > 262144, "the fixture was not larger than the read window");
    assert.equal((await fetchCodexUsage()).five_hour.used_pct, 77);

    // Case 2: the same reading at the START, pushed out of the window by the same filler. Unknown.
    writeFile(file, rolloutLine(77, 79) + filler.repeat(1200));
    assert.equal((await fetchCodexUsage()).unknown, true,
      "a rate_limits line outside the 256KB tail was somehow found — the read window changed");
  });
});

// ── defaultFetchCodex: live source first, rollout as the fallback ────────────

test("with no auth store, the codex fetcher FALLS BACK to the rollout and never fetches", async () => {
  // `defaultFetchCodex` is `(await fetchChatGptUsageLive()) || (await fetchCodexUsage())`. With no token the
  // live half returns before it would reach the network, which is what makes this assertable at all: no
  // fetchImpl is injected anywhere, so a request would be a real one.
  await withSealedHome(async () => {
    writeFile(path.join(process.env.CODEX_HOME, "sessions", "rollout-x.jsonl"), rolloutLine(55, 66));

    const posted = [];
    await collectOnce({
      fetchAnthropic: async () => null,   // out of scope for this test
      post: async (row) => { posted.push(row); },
    });

    assert.equal(posted.length, 1, "the codex source posted nothing");
    assert.equal(posted[0].five_hour.used_pct, 55, "the rollout fallback did not supply the number");
  });
});

// A JWT-shaped fixture whose payload claims openai.com, which is what `extractOpenAiToken` requires. It is
// three dots' worth of base64 and signs nothing; no real token appears in this file.
const OPENAI_SHAPED_FIXTURE_TOKEN = [
  "eyJhbGciOiJub25lIn0",
  Buffer.from(JSON.stringify({ iss: "https://auth.openai.com" })).toString("base64url"),
  "not-a-signature",
].join(".");

test("the LIVE source wins over the rollout when both can answer", async () => {
  // This is why `defaultFetchCodex` exists: the rollout is a snapshot written by the last response, so it can
  // be hours stale, while the live endpoint is current. If the order inverted, the dashboard would show an old
  // number as if it were now - and quota drives failover decisions.
  //
  // `defaultFetchCodex` takes no injection, so `globalThis.fetch` is stubbed for the duration and the stub
  // asserts it was USED - a real request could not pass unnoticed.
  await withSealedHome(async () => {
    writeFile(path.join(process.env.CODEX_HOME, "auth.json"),
      JSON.stringify({ tokens: { access_token: OPENAI_SHAPED_FIXTURE_TOKEN } }));
    writeFile(path.join(process.env.CODEX_HOME, "sessions", "rollout-stale.jsonl"), rolloutLine(11, 12));

    const realFetch = globalThis.fetch;
    const requested = [];
    globalThis.fetch = async (url) => {
      requested.push(String(url));
      return {
        ok: true,
        json: async () => ({
          rate_limit: {
            primary_window: { limit_window_seconds: 18000, used_percent: 73 },
            secondary_window: { limit_window_seconds: 604800, used_percent: 44 },
          },
        }),
      };
    };
    try {
      const posted = [];
      await collectOnce({ fetchAnthropic: async () => null, post: async (row) => { posted.push(row); } });
      assert.equal(requested.length, 1, "the live source was never asked");
      assert.match(requested[0], /^https:\/\//, "the live source was asked over something other than https");
      assert.equal(posted.length, 1);
      assert.equal(posted[0].five_hour.used_pct, 73, "the stale rollout answered instead of the live source");
      assert.equal(posted[0].weekly.used_pct, 44);
    } finally {
      globalThis.fetch = realFetch;
    }
  });
});

test("with neither an auth store nor a rollout, the codex source posts UNKNOWN rather than nothing", async () => {
  // Posting `unknown` is deliberate - the UI shows a source it cannot read, instead of silently omitting it.
  await withSealedHome(async () => {
    const posted = [];
    await collectOnce({ fetchAnthropic: async () => null, post: async (row) => { posted.push(row); } });
    assert.equal(posted.length, 1);
    assert.equal(posted[0].unknown, true);
  });
});
