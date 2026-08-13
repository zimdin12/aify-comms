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
  return String(session?.environmentId || session?.environment_id || session?.envId || session?.env_id || 'unassigned');
}

export function sessionRuntime(session) {
  return String(session?.runtime || session?.runtimeKind || session?.kind || 'runtime');
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
  const roots = env?.cwdRoots || env?.cwd_roots || env?.roots || env?.workspaceRoots || [];
  return Array.isArray(roots) ? roots.filter(Boolean) : [];
}

export function runPendingControlCount(run) {
  return (run?.controls || []).filter((control) => ['pending', 'claimed'].includes(String(control.status || '').toLowerCase())).length;
}

export function sessionId(session) {
  return String(session?.id || session?.sessionId || session?.session_id || '');
}
export function sessionAgentId(session) {
  return String(session?.agentId || session?.agent_id || session?.agent || '');
}
export function runTargetAgent(run) {
  return String(run?.targetAgentId || run?.target_agent || run?.agentId || run?.agent_id || '');
}
