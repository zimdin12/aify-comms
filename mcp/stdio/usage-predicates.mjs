// Pure helpers for `comms_usage`, in a sibling module so the tool keeps a one-name export surface.
//
// `usage-tool.test.js` asserts that usage-tool.mjs exports only `registerUsageTool`, and that is the
// convention worth keeping: the tool modules register a tool, and anything with logic worth testing
// directly lives beside them. `doctor-predicates.js` and `reap-managed-survivors.js` are the same
// shape, and `doctor.js` was untestable until its predicates moved out.

/**
 * The caller's own quota line, as text.
 *
 * WHY IT IS A FUNCTION. It was built inline and reported LESS than the pool table ten lines above it
 * in the same message. `fmt` prints both windows and the severity whenever it is not "normal"; this
 * line printed the weekly figure and a flag that only fires on `critical`, so the middle state was
 * invisible on the one line addressed to the agent reading it.
 *
 * MEASURED on the live fleet 2026-08-27: of 47 agents, 21 had `poolSeverity: "warning"` and every one
 * of them showed `poolWeeklyPctLeft: 16`. The severity is driven by the WORSE of the five-hour and
 * weekly windows (`usage_openai.py`: `worst = max(five_hour, weekly)`), so those agents were told
 * "16% weekly left" while the window actually near its limit was the five-hour one. Nothing was wrong
 * with either number; they answer different questions and only one was being asked.
 *
 * `poolSeverity` had NO consumer anywhere in the repo -- serialised onto every agent row by
 * `records.py` and read by nothing. This is that reader.
 *
 * SEVERITY SUBSUMES `quotaCritical`, which is the boolean form of `severity === "critical"`. That
 * field stays in the payload: it is part of what `/agents` emits and removing an emitted field is an
 * API decision, not a tidy-up. It is simply no longer the only thing this line can say.
 */
export function personalQuotaLine(agentId, agent) {
  const source = String(agent?.usageSource || "").trim();
  if (!agentId || !source) return "";
  const weekly = agent?.poolWeeklyPctLeft;
  // "?" not "0%": a missing percentage rendered as zero would report an exhausted pool when nothing
  // is known about it. Same rule the pool table above follows, and the reason this file exists.
  const left = weekly === null || weekly === undefined ? "?" : `${weekly}%`;
  // `quotaCritical` is the FALLBACK, not the source. It is the boolean form of `severity === "critical"`,
  // and records.py derives both from one pool so they cannot disagree -- but if severity ever arrives
  // missing, a formatter that then reports NOTHING has failed in the wrong direction for a quota warning.
  const severity = String(agent?.poolSeverity || "").trim().toLowerCase() || (agent?.quotaCritical ? "critical" : "");
  const tag = severity && severity !== "normal" ? ` [${severity}]` : "";
  return `\nYou (${agentId}) → ${source}: ${left} weekly left${tag}`;
}
