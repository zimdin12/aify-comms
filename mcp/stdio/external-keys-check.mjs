// The `external-keys` check: are the keys issued to other machines actually doing anything?
//
// `EXTERNAL_KEYS` gives each other machine its own key, which may only send messages and whose
// messages are marked with that machine's name (service/api_core/external_keys.py). Two ways to set it
// and get nothing, both silent from the outside: set it on a service with no `API_KEY`, where nothing
// is authenticated and the other machine can simply omit its key; or mistype an entry, which the
// service refuses and only logs. The service reports both on `/health` as counts, and this reads them.
//
// Extracted so it can be executed, like every check beside it: importing `doctor.js` RUNS the doctor.
// Every collaborator is a parameter with no default.

/**
 * @param deps.get  fetch a JSON path from the service, or null when it does not answer
 * @param deps.add  record a check result
 */
export async function checkExternalKeys({ get, add }) {
  const health = await get("/health");
  if (!health) {
    // No evidence is not a pass. The `service` row already says why the service did not answer.
    return add("external-keys", false, "unknown", "The service did not answer /health, so nothing was checked.",
      "See the `service` row.");
  }
  const keys = health.externalKeys;
  if (!keys) {
    return add("external-keys", true, "none-configured", "No keys are issued to other machines (EXTERNAL_KEYS is unset).", "");
  }
  if (!keys.enforced) {
    return add("external-keys", false, "unenforced",
      `${keys.configured} external machine key(s) are set, but API_KEY is not, so nothing is authenticated and `
        + "the keys restrict nothing: another machine can send with no key at all, as anyone.",
      "Set API_KEY in .env and restart the service (`docker compose up -d`), then re-run install.sh for each client.");
  }
  if (keys.rejected > 0) {
    return add("external-keys", false, "entries-refused",
      `${keys.rejected} EXTERNAL_KEYS entr${keys.rejected === 1 ? "y was" : "ies were"} refused and grant nothing; `
        + `${keys.configured} key(s) work.`,
      "The reason is logged by name, without the key: `docker compose logs service | grep 'EXTERNAL_KEYS entry refused'`.");
  }
  return add("external-keys", true, "enforced", `${keys.configured} external machine key(s), enforced.`, "");
}
