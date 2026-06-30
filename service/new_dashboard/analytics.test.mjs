import { test } from 'node:test';
import assert from 'node:assert/strict';
import { analyticsSeries, trafficChartHtml, statCardsHtml, healthGridHtml, runStatusMixHtml, rangeSelectorHtml, rangeDef, opsKpisHtml, dispatchOutcomesHtml, agentLeaderboardHtml, busiestChannelsHtml, failureReasonsHtml, fleetPulseHtml, pulseWindowSelectorHtml, PULSE_WINDOWS } from './analytics.js';

const sample = {
  messagesPerHour: Array.from({ length: 24 }, (_, i) => ({ label: `${i}:00`, count: i })),
  messagesPerDay: [{ label: 'Mon', count: 3 }, { label: 'Tue', count: 7 }],
  messageTotal: 120, runTotal: 30,
  runsByStatus: { completed: 20, failed: 4, running: 2, cancelled: 1 },
  spawnRequestsByStatus: { failed: 1 }, spawnRequestTotal: 5,
  liveAgents: 6, onlineAgents: 4, workingAgents: 2, onlineEnvironments: 3,
};

test('rangeDef falls back to hour', () => {
  assert.equal(rangeDef('day').key, 'day');
  assert.equal(rangeDef('nonsense').key, 'hour');
});

test('analyticsSeries picks the series for the range', () => {
  assert.equal(analyticsSeries(sample, 'hour').items.length, 24);
  assert.equal(analyticsSeries(sample, 'day').items.length, 2);
  assert.equal(analyticsSeries(sample, 'day').label, 'last 30 days');
});

test('trafficChartHtml emits an svg with bars/line and a summary total', () => {
  const html = trafficChartHtml(sample, 'hour');
  assert.match(html, /<svg class="chart-svg"/);
  assert.match(html, /class="chart-bar"/);
  assert.match(html, /class="chart-line"/);
  // total of 0..23 = 276
  assert.match(html, /<b>276<\/b><span>Total messages<\/span>/);
});

test('trafficChartHtml is safe on empty data', () => {
  const html = trafficChartHtml({}, 'hour');
  assert.match(html, /<svg/);
  assert.match(html, /<b>0<\/b><span>Total messages<\/span>/);
});

test('statCardsHtml shows messages, runs, and a warn tone for failures', () => {
  const html = statCardsHtml(sample);
  assert.match(html, /120/);
  assert.match(html, /class="sc warn"/); // 4 failed + 1 cancelled
  assert.match(html, /Failed \/ Cancelled/);
});

test('healthGridHtml shows live capacity (agents + envs), no spawn-throughput dupes', () => {
  const html = healthGridHtml(sample);
  assert.match(html, /Live agents/);
  assert.match(html, /Online envs/);
  // Capacity-now is live-capacity only; spawn failures live in the KPI grid, not here.
  assert.ok(!/Spawn failures/.test(html), 'spawn failures must not be duplicated in capacity grid');
});

test('runStatusMixHtml renders one bar per non-zero status, sorted', () => {
  const html = runStatusMixHtml(sample.runsByStatus);
  assert.match(html, /status-bar-row/);
  // cancelled before completed before failed before running (alpha sort)
  assert.ok(html.indexOf('cancelled') < html.indexOf('completed'));
  assert.ok(html.indexOf('completed') < html.indexOf('running'));
});

test('runStatusMixHtml empty state', () => {
  assert.match(runStatusMixHtml({}), /No tracked runs/);
});

test('rangeSelectorHtml marks the active range', () => {
  const html = rangeSelectorHtml('day');
  assert.match(html, /class="active" data-analytics-range="day"/);
});

const fleet = {
  successRate: 80, runsCompleted: 20, runsFailed: 5,
  openReplyContracts: 3, overdueReplyContracts: 1, fleetMedianReplyMinutes: 42,
  dispatchOutcomes: [{ date: '2026-06-16', completed: 4, failed: 1 }, { date: '2026-06-17', completed: 6, failed: 0 }],
  agentLeaderboard: [{ agent: 'alpha', completed: 9, failed: 1, total: 10, successRate: 90 }, { agent: 'beta', completed: 2, failed: 3, total: 5, successRate: 40 }],
  busiestChannels: [{ channel: 'general', count: 12 }, { channel: 'ops', count: 4 }],
  failureReasons: [{ reason: 'timeout waiting for reply', count: 3 }],
};

test('opsKpisHtml renders success rate with tone + overdue contracts', () => {
  const html = opsKpisHtml(fleet);
  assert.match(html, /80%/);
  assert.match(html, /Dispatch success/);
  assert.match(html, /class="sc warn"/); // 80% success → warn tone
  assert.match(html, /class="sc bad"/);  // 1 overdue contract → bad tone
  assert.match(html, /Median reply/);
});

test('opsKpisHtml tolerates null success rate / median', () => {
  const html = opsKpisHtml({ successRate: null, fleetMedianReplyMinutes: null });
  assert.match(html, /—/);
});

test('dispatchOutcomesHtml stacks completed + failed with legend', () => {
  const html = dispatchOutcomesHtml(fleet);
  assert.match(html, /outcome-comp/);
  assert.match(html, /outcome-fail/);
  assert.match(html, /Completed/);
  assert.match(dispatchOutcomesHtml({}), /No dispatch runs/);
});

test('agentLeaderboardHtml ranks agents and tones their success rate', () => {
  const html = agentLeaderboardHtml(fleet);
  assert.match(html, /alpha/);
  assert.match(html, /lb-sr good/); // 90%
  assert.match(html, /lb-sr bad/);  // 40%
  assert.match(agentLeaderboardHtml({}), /No dispatch activity/);
});

test('busiestChannelsHtml prefixes channel names with #', () => {
  const html = busiestChannelsHtml(fleet);
  assert.match(html, /#general/);
  assert.match(busiestChannelsHtml({}), /No channel traffic/);
});

test('failureReasonsHtml lists reasons; celebrates a clean window', () => {
  assert.match(failureReasonsHtml(fleet), /timeout waiting for reply/);
  assert.match(failureReasonsHtml(fleet), /3×/);
  assert.match(failureReasonsHtml({}), /No failed or cancelled runs/);
});

test('pulseWindowSelectorHtml marks the active window and offers all windows', () => {
  const html = pulseWindowSelectorHtml(180);
  assert.equal(PULSE_WINDOWS.length, 7);
  assert.match(html, /class="active" data-pulse-window="180"/);
  assert.match(html, /data-pulse-window="10"/);
  assert.match(html, /data-pulse-window="1440"/);
});

test('fleetPulseHtml renders KPIs + an online-agent board with last-worked', () => {
  const data = {
    ok: true, windowMinutes: 60,
    messages: { count: 42, perHour: 42 },
    onlineAgents: 3, workingNow: 1, fleetWorkingMinutes: 12.5, fleetUtilizationPct: 35,
    openReplyContracts: 2, overdueReplyContracts: 1,
    agents: [
      { id: 'alpha', role: 'manager', runtime: 'claude-code', mode: 'managed', status: 'working', lastWorkedAt: null, workingNow: true, messagesInWindow: 9, workingMinutesInWindow: 12.5 },
      { id: 'beta', role: 'coder', runtime: 'codex', mode: 'resident', status: 'online', lastWorkedAt: '2026-06-18T00:00:00Z', workingNow: false, messagesInWindow: 3, workingMinutesInWindow: 0 },
    ],
  };
  const html = fleetPulseHtml(data, 60);
  assert.match(html, /Comms performance/);
  assert.match(html, /42\/hr/);
  assert.match(html, /35%/);
  assert.match(html, /data-pulse-window="60"/);
  assert.match(html, /data-chat-open="dm:alpha"/);
  assert.match(html, /working now/);          // alpha is currently working
  assert.match(html, /9 msg · 12.5m work/);   // in-window activity
  assert.match(html, /1 overdue/);
});

test('fleetPulseHtml is safe on loading / error / empty states', () => {
  assert.match(fleetPulseHtml(null, 60), /Loading fleet pulse/);
  assert.match(fleetPulseHtml({ ok: false }, 60), /Pulse unavailable/);
  assert.match(fleetPulseHtml({ ok: true, agents: [] }, 30), /No online agents/);
});
