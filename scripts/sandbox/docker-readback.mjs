// Whether one Docker resource a witness run created is gone, judged from Docker's own answer to inspecting it.
//
// ABSENT IS DOCKER SAYING SO ABOUT THIS NAME. Anything else (a daemon that cannot be reached, a timeout, a message about
// another name) is UNKNOWN, and a cleanup with an unknown resource is not complete (review E3).

/** The command that inspects one owned resource by name. */
export function inspectArgs({ kind, name }) {
  return kind === "image" ? ["image", "inspect", name] : kind === "network" ? ["network", "inspect", name] : ["container", "inspect", name];
}

/** "present", "absent" or "unknown" for one inspect answer `{status, out}`. */
export function classifyReadBack({ kind, name }, { status, out }) {
  if (status === 0) return "present";
  const n = String(name).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const notFound = kind === "network" ? new RegExp(`^Error response from daemon: network ${n} not found\\s*$`, "m")
    : kind === "image" ? new RegExp(`^Error(?: response from daemon)?: No such image: ${n}(?::latest)?\\s*$`, "m")
      : new RegExp(`^Error(?: response from daemon)?: No such (?:container|object): ${n}\\s*$`, "m");
  return notFound.test(String(out)) ? "absent" : "unknown";
}
