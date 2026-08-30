// Writing this service's entry into the shared registry at `~/.aify/services.json`.
//
// The registry is how a launcher learns a service exists. Each service's installer owns its OWN entry
// and nothing else — aify-comms adds `aify-comms`, a graph service adds its own, and neither touches
// the other's. That ownership rule is the whole reason this is an upsert of one key rather than a
// write of the file.
//
// Pure: text in, text out. The caller owns the filesystem. Same reason as the bridge's other predicate
// modules — logic reachable only through a shell script can only fail in production.
//
// The counterpart reader lives in the aify-wrapper package (`lib/registry.mjs`). This file must keep
// producing what that parser accepts; the shape is documented in that package's docs/REGISTRY.md.

/** The only schema version we write. A file at another version is left alone rather than rewritten. */
export const REGISTRY_VERSION = 1;

const isPlainObject = (value) => typeof value === "object" && value !== null && !Array.isArray(value);

/**
 * Add or replace one service's entry, preserving every other service's.
 *
 * @param {string} existingText  current file contents ("" when absent)
 * @param {string} serviceName   the key this service owns
 * @param {{endpoint: string, endpointEnv: string[], keyEnv: string[], mcp: object[]}} entry
 * @returns {{ok: boolean, text?: string, errors: string[]}}
 */
// The READER, imported rather than reimplemented. `parseRegistry` owns what a valid entry is;
// a second copy of those rules here would agree until one of them was fixed. It comes from the
// aify-wrapper package this repo already pins, and `install.sh` runs `npm install` (2781) before
// it registers the service (2801), so a missing package fails loudly at install rather than
// silently skipping the check.
import { parseRegistry } from "aify-wrapper/lib/registry.mjs";

/**
 * The registry key THIS service owns, and the name it is known by to anyone reading the registry.
 *
 * One owner. It was a bare const in `register-service-cli.mjs`, which was fine while exactly one
 * file needed it; the bridge now has to ask aify-env "are you advertising to ME?", and a second
 * hand-typed copy of an identity is how two files come to disagree about who you are.
 */
export const SERVICE_NAME = "aify-comms";

export function upsertService(existingText, serviceName, entry) {
  const errors = [];
  if (!serviceName || typeof serviceName !== "string") errors.push("serviceName is required");
  if (!entry || !entry.endpoint) errors.push("entry.endpoint is required");
  if (errors.length) return { ok: false, errors };

  const raw = typeof existingText === "string" ? existingText.trim() : "";
  let current = { version: REGISTRY_VERSION, services: {} };

  if (raw !== "") {
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch (error) {
      // REFUSE rather than overwrite. A registry we cannot read may hold another service's entry, and
      // replacing it would uninstall that service from every launcher installed afterwards — silently,
      // and at the moment somebody reinstalls something unrelated.
      return { ok: false, errors: [`refusing to rewrite an unreadable registry: ${error.message}`] };
    }
    if (!isPlainObject(parsed) || !isPlainObject(parsed.services)) {
      return { ok: false, errors: ["refusing to rewrite a registry that is not in the expected shape"] };
    }
    if (parsed.version !== REGISTRY_VERSION) {
      return {
        ok: false,
        errors: [`registry is version ${JSON.stringify(parsed.version)}; this installer writes ${REGISTRY_VERSION}`],
      };
    }
    current = parsed;
  }

  const services = { ...current.services, [serviceName]: normaliseEntry(entry) };

  // REFUSE TO WRITE WHAT THE READER WOULD REJECT. The guard above protects another service's entry
  // from an unreadable EXISTING file; without this one the same harm arrives through the other
  // door, because the reader refuses the WHOLE file on one bad entry. Measured: seed a registry
  // with `other-service`, then write an aify-comms entry whose mcp server has no name -- the write
  // reports ok and `other-service` disappears from every launcher that reads the file afterwards.
  //
  // Only OUR entry is judged. Validating the merged file would let a third party's pre-existing
  // damage block a registration that is itself correct, which is a worse failure than the one this
  // prevents: it would make somebody else's bad entry uninstall ours.
  const ours = parseRegistry(stableJson({ version: REGISTRY_VERSION, services: { [serviceName]: services[serviceName] } }));
  if (!ours.ok) {
    return {
      ok: false,
      errors: [
        `refusing to write an entry no launcher could read: ${ours.errors.join('; ')}`,
      ],
    };
  }

  return { ok: true, text: `${stableJson({ version: REGISTRY_VERSION, services })}\n`, errors: [] };
}

function normaliseEntry(entry) {
  const normalised = {
    endpoint: String(entry.endpoint).trim(),
    endpointEnv: [...(entry.endpointEnv ?? [])],
    keyEnv: [...(entry.keyEnv ?? [])],
    mcp: (entry.mcp ?? []).map((server) => ({
      name: server.name,
      command: server.command,
      args: [...(server.args ?? [])],
    })),
  };
  // Only when true. Writing `strictMcp: false` everywhere would make an opt-in that nobody chose look
  // like a decision somebody made.
  if (entry.strictMcp === true) normalised.strictMcp = true;
  return normalised;
}

/**
 * JSON with sorted keys and a fixed indent.
 *
 * Two installs with the same inputs must produce a byte-identical file. Without that, every reinstall
 * shows as a change to anything watching the registry — including the launcher fingerprint, which
 * would then report every wrapper stale after any unrelated reinstall.
 */
function stableJson(value, indent = "") {
  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    const inner = value.map((item) => `${indent}  ${stableJson(item, `${indent}  `)}`).join(",\n");
    return `[\n${inner}\n${indent}]`;
  }
  if (isPlainObject(value)) {
    const keys = Object.keys(value).sort();
    if (keys.length === 0) return "{}";
    const inner = keys
      .map((key) => `${indent}  ${JSON.stringify(key)}: ${stableJson(value[key], `${indent}  `)}`)
      .join(",\n");
    return `{\n${inner}\n${indent}}`;
  }
  return JSON.stringify(value ?? null);
}
