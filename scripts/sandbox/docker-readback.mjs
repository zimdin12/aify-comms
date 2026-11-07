// Whether one Docker resource a witness run created is gone, judged from Docker's own answer.
// Missing evidence, cancellation, daemon failures and another name's error are UNKNOWN.

const missingPatterns = Object.freeze({
  container: (name) => `No such (?:container|object): ${name}`,
  network: (name) => `network ${name} not found`,
  image: (name) => `No such image: ${name}(?::latest)?`,
  volume: (name) => `(?:get ${name}: no such volume|No such volume: ${name})`,
});

function resourcePattern({ kind, name }) {
  if (!Object.hasOwn(missingPatterns, kind)) throw new TypeError(`unsupported Docker resource kind: ${kind}`);
  if (typeof name !== 'string' || !name.trim()) throw new TypeError('Docker resource name is required');
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`^Error(?: response from daemon)?: ${missingPatterns[kind](escaped)}\\s*$`, 'm');
}

/** The command that inspects one owned resource by name. */
export function inspectArgs(target) {
  resourcePattern(target);
  return [target.kind, 'inspect', target.name];
}

/** "present", "absent" or "unknown" for one direct Docker process answer `{status, out}`. */
export function classifyReadBack(target, { status, out }) {
  const notFound = resourcePattern(target);
  if (status === 0) return 'present';
  if (status !== 1) return 'unknown';
  return notFound.test(String(out)) ? 'absent' : 'unknown';
}
