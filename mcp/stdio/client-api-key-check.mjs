// The service demands an API key and an installed client holds none.
//
// WHY IT EXISTS, and the honest version: it was built on 2026-09-07 from a diagnosis that turned out
// to be WRONG. A hermes agent was returning HTTP 401 on every outbound call -- its own console said
// "Ping was not sent: aify-comms rejected it with HTTP 401" -- and a `grep -A 20` of the aify-comms
// block in `~/.hermes/config.yaml` found no key. The key was on line 22. The grep stopped at 20, the
// zero looked like a finding, and a five-day fleet outage was inferred from it. The config carries a
// working key; the real cause of that agent's 401 is UNRESOLVED.
//
// THE CHECK SURVIVED ITS OWN FALSE PREMISE, which is the argument for keeping it. Everything the
// mistake relied on was true: the service does refuse unauthenticated calls, a keyless client would
// 401 on every call, and NOTHING would have reported it. Claude and hermes hold their keys in
// different files, so half a fleet can go mute while every status row stays green -- a quiet agent is
// indistinguishable from an idle one, because `lastSeen` refreshes on registration and `Last
// produced` only says when an agent last SENT. The failure mode is real even though that instance of
// it was not.
//
// IT FIRES ONLY ON THE COMBINATION, like `api-exposure` and for the same reason. Running with no
// `API_KEY` is a configuration, not a defect, and a client holding no key is correct against such a
// service. What is never correct is a service that refuses unauthenticated calls plus a client with
// nothing to authenticate with.
//
// ASKED WITHOUT A KEY, DELIBERATELY. The probe must be an unauthenticated request: asking with a key
// can only ever answer "yes, with a key", which is not the question. `api-exposure` learned this the
// same way and carries its own fetch for it.
//
// AND IT READS THE BLOCK, NEVER A WINDOW OF IT. The bug that produced the false diagnosis is the one
// this module must not repeat: `entryCarriesKey` walks the entry to its end by indentation rather
// than sampling N lines after it.

import { API_KEY_ENV_NAMES } from "./aify-service-endpoint.mjs";

/**
 * Does this client's `aify-comms` MCP entry carry one of the key names the bridge actually reads?
 *
 * SCOPED TO THE ENTRY, never the whole file, and that is the difference between a check and a
 * rumour. `~/.claude.json` holds every MCP server the operator has ever installed; a file-wide search
 * for `AIFY_API_KEY` passes on a key belonging to somebody else's server and reports our entry
 * healthy. The population a gate reads has to be the population it judges.
 *
 * @param {string} text     the config file's contents
 * @param {"json"|"yaml"} format
 * @returns {boolean|null}  null when the entry is absent or the file cannot be parsed -- that is
 *                          "no evidence", which the verdict must not read as a pass
 */
export function entryCarriesKey(text, format) {
  const body = String(text || "");
  if (!body.trim()) return null;
  return format === "json" ? jsonEntryCarriesKey(body) : yamlEntryCarriesKey(body);
}

function jsonEntryCarriesKey(body) {
  let parsed;
  try {
    parsed = JSON.parse(body);
  } catch {
    return null;
  }
  const entry = parsed?.mcpServers?.["aify-comms"];
  if (!entry) return null;
  const env = entry.env || {};
  return API_KEY_ENV_NAMES.some((name) => String(env[name] || "").trim());
}

/**
 * The `aify-comms:` block of a hermes config, by indentation.
 *
 * No YAML parser, and that is a decision rather than laziness: this repo ships no YAML dependency,
 * and the question is narrow enough to answer by structure -- take the lines under `aify-comms:` that
 * are indented deeper than it, and stop at the first that is not. The same walk `install.sh`'s own
 * `configWithAifyEntry` uses to replace the block, so the two agree about where it ends.
 */
function yamlEntryCarriesKey(body) {
  const lines = body.replace(/\s*$/, "").split(/\r?\n/);
  const start = lines.findIndex((line) => /^[ \t]+aify-comms:[ \t]*$/.test(line));
  if (start < 0) return null;
  const baseIndent = (lines[start].match(/^[ \t]+/) || [""])[0].length;
  const block = [];
  for (let i = start + 1; i < lines.length; i += 1) {
    if (lines[i].trim() === "") continue;
    const indent = (lines[i].match(/^[ \t]*/) || [""])[0].length;
    if (indent <= baseIndent) break;
    block.push(lines[i]);
  }
  return block.some((line) => {
    const named = API_KEY_ENV_NAMES.find((name) => line.includes(`${name}:`));
    if (!named) return false;
    const value = line.slice(line.indexOf(`${named}:`) + named.length + 1).trim();
    // An empty or quote-only value is not a key. `AIFY_API_KEY: ""` would satisfy a name search and
    // authenticate with nothing, which is the state this check exists to find.
    return Boolean(value.replace(/^["']|["']$/g, "").trim());
  });
}

/**
 * @param {object} deps
 * @param {boolean|null} deps.serviceRequiresKey  from an UNAUTHENTICATED probe; null = could not ask
 * @param {Array<{name: string, path: string, carriesKey: boolean|null}>|null} deps.clients
 *        one row per installed client config found on this host; null = could not look
 */
export function clientApiKeyVerdict({ serviceRequiresKey = null, clients = null } = {}) {
  if (serviceRequiresKey === null || serviceRequiresKey === undefined) {
    return {
      ok: false,
      code: "unknown-all",
      detail: "could not ask the service whether it requires an API key, so no client was judged "
        + "against anything.",
      fix: "Check the `service` row above — if the service is unreachable this row cannot answer.",
    };
  }
  if (serviceRequiresKey === false) {
    return {
      ok: true,
      code: "no-key-required",
      detail: "the service accepts unauthenticated calls, so a client holding no key is correct. "
        + "`api-exposure` is the row that reports what running open costs.",
      fix: "",
    };
  }
  if (clients === null || clients === undefined) {
    return {
      ok: false,
      code: "unknown-all",
      detail: "the service requires an API key, but no installed client config could be read, so "
        + "nothing was verified.",
      fix: "Check that this host has an installed client; `bridge-installed` reports that.",
    };
  }
  if (!clients.length) {
    return {
      ok: true,
      code: "none-installed",
      detail: "the service requires an API key and no client config is installed on this host.",
      fix: "",
    };
  }

  const keyless = clients.filter((client) => client.carriesKey === false);
  const unreadable = clients.filter((client) => client.carriesKey !== true && client.carriesKey !== false);

  if (keyless.length) {
    const names = keyless.map((client) => client.name).join(", ");
    return {
      ok: false,
      code: "client-has-no-key",
      detail: `the service refuses unauthenticated calls, and the aify-comms MCP entry for ${names} `
        + `carries no API key — every call those agents make comes back HTTP 401. `
        + keyless.map((client) => client.path).join("; "),
      fix: `Re-run install.sh --client ${keyless[0].name}, then restart that runtime's agents. `
        + "It replaces the existing entry and resolves the key from .env.",
    };
  }
  if (unreadable.length) {
    return {
      ok: false,
      code: "partial",
      detail: "the service requires an API key and "
        + `${unreadable.map((client) => client.name).join(", ")} could not be read, so those clients `
        + "were not verified. " + unreadable.map((client) => client.path).join("; "),
      fix: "Check the file exists and is valid; re-run install.sh for that client to rewrite it.",
    };
  }
  return {
    ok: true,
    code: "keys-present",
    detail: `the service requires an API key and all ${clients.length} installed client config(s) `
      + "carry one.",
    fix: "",
  };
}
