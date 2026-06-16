import { test } from 'node:test';
import assert from 'node:assert/strict';
import { analyticsSeries, trafficChartHtml, statCardsHtml, healthGridHtml, runStatusMixHtml, rangeSelectorHtml, rangeDef } from './analytics.js';

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

test('healthGridHtml shows live agents + flags failed spawns bad', () => {
  const html = healthGridHtml(sample);
  assert.match(html, /Live agents/);
  assert.match(html, /class="health-card bad"/); // 1 failed spawn
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
