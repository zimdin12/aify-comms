// Editing a DEFINED agent: its definition fields change by ONE definition request its host applies (P0 C5), so a
// Save either queues the whole change or nothing of it. The id is not offered: it names the definition file, the
// host's stop record and the session folder, and no host renames it (env-senior-dev, aify-env e70321d). The display
// name is offered instead. A running agent keeps what it started with; the change applies at its next start.
//
// The form and the patch are pure; agent-session-actions.mjs submits it.

import { environmentRuntimes } from './record-fields.mjs';
import { esc } from './util.js';

/** The form fields, in order, each with the definition field it sets. `runtime` sets `harness` through the map. */
const TEXT_FIELDS = [
  ['name', 'Display name', ''],
  ['role', 'Role', ''],
  ['workspace', 'Workspace', 'The folder it starts in'],
  ['model', 'Model', 'Blank: the harness default'],
  ['effort', 'Effort', 'Blank: the harness default'],
];

/** PURE. The runtime the DEFINITION names, which is what its next start runs. The worker's own `runtime` is what it
 * started with, and differs from the definition after an edit (review of 3bfd62a8): defaulting to it reversed one. */
export function definitionRuntime(agent) {
  const harnessOf = agent.definition?.harnessOfRuntime || {};
  const harness = agent.definition?.fields?.harness;
  return Object.keys(harnessOf).find((runtime) => harnessOf[runtime] === harness) || String(agent.runtime || '');
}

/** PURE. The runtimes this agent's host can launch that a definition can name, and the one its definition names. */
export function definitionRuntimeOptions(agent, environments) {
  const harnessOf = agent.definition?.harnessOfRuntime || {};
  const owner = agent.definition?.ownerMachineId;
  const current = definitionRuntime(agent);
  const advertised = environments.filter((env) => env.machineId === owner)
    .flatMap((env) => environmentRuntimes(env).filter((r) => r.available !== false).map((r) => r.runtime));
  return [...new Set([current, ...advertised])].filter((runtime) => runtime && harnessOf[runtime]);
}

/** PURE. The form's definition fieldset. */
export function definitionFieldset(agent, environments) {
  const fields = agent.definition?.fields || {};
  const runtime = definitionRuntime(agent);
  const options = definitionRuntimeOptions(agent, environments)
    .map((rt) => `<option value="${esc(rt)}"${rt === runtime ? ' selected' : ''}>${esc(rt)}</option>`).join('');
  const inputs = TEXT_FIELDS.map(([field, label, hint]) =>
    `<label class="settings-label">${esc(label)}<input id="edit-def-${field}" type="text" value="${esc(fields[field] ?? '')}"${hint ? ` placeholder="${esc(hint)}"` : ''}></label>`).join('');
  return `<fieldset class="agent-edit-env">
        <legend>Definition, kept by aify-env on ${esc(agent.definition?.ownerMachineId || 'its host')}</legend>
        ${inputs}
        <label class="settings-label">Runtime<select id="edit-def-runtime">${options}</select></label>
        <label class="settings-label">Instructions<textarea id="edit-def-instructions" rows="5">${esc(fields.instructions ?? '')}</textarea></label>
        <p class="subtle">Saved as one change its host applies. A running agent keeps what it started with until it is next started; Restart applies it now. History shows whether the host applied it.</p>
      </fieldset>`;
}

/** PURE. The definition patch: only the fields the form changed, or {} when nothing did. */
export function definitionPatch(agent, form) {
  const fields = agent.definition?.fields || {};
  const harnessOf = agent.definition?.harnessOfRuntime || {};
  const wanted = Object.fromEntries([...TEXT_FIELDS.map(([field]) => field), 'instructions']
    .filter((field) => field in form).map((field) => [field, form[field]]));
  if (form.runtime && harnessOf[form.runtime]) wanted.harness = harnessOf[form.runtime];
  return Object.fromEntries(Object.entries(wanted).filter(([field, value]) => value !== (fields[field] ?? '')));
}

/** The form's values as `definitionPatch` reads them, exactly as typed; a field the form does not hold is left out.
 * NOT TRIMMED: a stored value with spaces is legal, and trimming it read as a change (review of 3bfd62a8). */
export function readDefinitionForm(byId) {
  const form = {};
  for (const field of [...TEXT_FIELDS.map(([f]) => f), 'runtime', 'instructions']) {
    const element = byId(`edit-def-${field}`);
    if (element) form[field] = element.value;
  }
  return form;
}
