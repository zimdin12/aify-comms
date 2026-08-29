// Reading a field off an API record when the API returns it under several names.
//
// Nine functions, and the subject is exactly that: `messageRunId` looks for `dispatchRunId`,
// `dispatch_run_id`, `runId`, `run_id`, `contractRunId` AND `contract_run_id`, because the payloads this
// dashboard consumes come from routes written at different times and the same field arrives camelCased,
// snake_cased, or under a legacy alias. Every one of these is a place where reading the field directly
// would have worked for one payload shape and silently produced '' for another.
//
// That is also why they are worth extracting first among the small clusters: the defect they exist to
// prevent is invisible in a passing render. A missing id does not throw — it produces an empty string, an
// unclickable row, or a filter that matches nothing, and the page still draws. The degenerate-input tests
// beside this file are the only thing that distinguishes "this record has no run" from "we looked under the
// wrong key".
//
// All nine are exported because app.js calls all nine directly (9, 5, 2, 11, 10, 1, 1, 1 and 4 call sites
// respectively) — `messageIdOf` included, which is called four times outside `messageId`. No export here
// exists to make testing convenient.
//
// No DOM, no module-scope state, no imports: these are pure readers over a plain object.

export function messageIdOf(m) { return String(m?.id || m?.messageId || m?.message_id || ''); }

export function asAgentArray(payload) {
  if (Array.isArray(payload.agents)) return payload.agents;
  return Object.entries(payload.agents || {}).map(([id, value]) => ({ id, ...value }));
}

export function sessionEnvironmentId(session) {
  // '' means "no binding", NOT a word. This answered 'unassigned' and every caller that guarded on
  // the value took the populated branch; see record-fields.test.mjs for what each one then did with
  // it. The Sessions rail names its own empty group, because the rail is what displays one.
  return String(session?.environmentId || '');
}

export function sessionRuntime(session) {
  // '' means "this session names no runtime", for the same reason. It answered the literal 'runtime'.
  return String(session?.runtime || '');
}

export function messageId(message) {
  return messageIdOf(message);
}

export function messageRunId(message) {
  return String(message?.dispatchRunId || message?.dispatch_run_id || message?.runId || message?.run_id || message?.contractRunId || message?.contract_run_id || '');
}

export function contractCategory(c) {
  return String(c.category || c.kind || (c.channel ? 'channel' : c.selfWake || c.self_wake ? 'self_wake' : 'direct')).toLowerCase();
}

export function environmentRoots(env) {
  const roots = env?.cwdRoots || [];
  return Array.isArray(roots) ? roots.filter(Boolean) : [];
}

export function runPendingControlCount(run) {
  return (run?.controls || []).filter((control) => ['pending', 'claimed'].includes(String(control.status || '').toLowerCase())).length;
}

export function sessionId(session) {
  return String(session?.id || '');
}
export function sessionAgentId(session) {
  return String(session?.agentId || '');
}
export function runTargetAgent(run) {
  return String(run?.targetAgentId || run?.target_agent || run?.agentId || run?.agent_id || '');
}

export function environmentRuntimes(env) {
  const runtimes = env?.runtimes || [];
  return Array.isArray(runtimes) ? runtimes
    .map((runtime) => typeof runtime === 'string' ? { runtime, available: true } : runtime)
    .filter((runtime) => runtime && runtime.runtime) : [];
}

export function asArray(payload, key) {
  const value = payload?.[key];
  if (Array.isArray(value)) return value;
  if (value && typeof value === 'object') return Object.entries(value).map(([id, item]) => ({ id, ...item }));
  return [];
}

export function contractActionable(contract) {
  const target = String(contract?.targetAgentId || '').trim();
  const current = String(contract?.state || '').toLowerCase();
  return Boolean(contract?.id && target && target !== 'dashboard' && !['answered', 'closed'].includes(current));
}
