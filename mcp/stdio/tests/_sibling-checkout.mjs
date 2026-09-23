// Where a sibling repo's checkout is, for the tests that compare against it.
//
// ONE RULE FOR EVERY CROSS-REPO TEST. They used to disagree: most read `AIFY_ENV_REPO` or
// `AIFY_WRAPPER_REPO` and then `~/projects/<name>`, one read `AIFY_ENV_DIR` instead, and one read
// no variable but also looked beside this checkout. So a checkout beside this one was found by one
// test and missed by the rest, and a variable that fixed four of them fixed nothing for the others.
//
// A NAMED CHECKOUT IS THE ONLY CANDIDATE. Falling back when the named tree lacks the file would
// judge a different checkout than the one asked for (the Python helper `env_repo` records the run
// that went green that way). Unnamed, the candidates are beside this repo, then ~/projects.
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = fileURLToPath(new URL("../../..", import.meta.url));
const ENV = { "aify-env": "AIFY_ENV_REPO", "aify-wrapper": "AIFY_WRAPPER_REPO" };

/**
 * The checkout of `name` holding `marker`, or `dir: null`. `looked` lists every place tried, for the
 * failure message.
 */
export function siblingCheckout(name, marker = "package.json") {
  const named = String(process.env[ENV[name]] || "").trim();
  const looked = named ? [named] : [path.join(REPO, "..", name), path.join(homedir(), "projects", name)];
  const dir = looked.find((candidate) => existsSync(path.join(candidate, marker))) ?? null;
  return { dir, looked, variable: ENV[name] };
}
