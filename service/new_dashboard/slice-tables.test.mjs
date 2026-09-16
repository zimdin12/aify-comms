// The table map is the only thing that decides which panel refreshes on a change, so it is checked
// against the two sources it must agree with: the database schema, and the loaders that fetch.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { LIVENESS_SLICES, SLICE_TABLES, slicesReading } from './slice-tables.mjs';

const schema = readFileSync(new URL('../schema.py', import.meta.url), 'utf8');
const SCHEMA_TABLES = new Set([...schema.matchAll(/CREATE TABLE IF NOT EXISTS (\w+)/g)].map((m) => m[1]));
const loaders = readFileSync(new URL('./slice-loaders.mjs', import.meta.url), 'utf8');

test('every table the map names exists in the schema', () => {
  assert.ok(SCHEMA_TABLES.size > 10, 'control: the schema walk found almost no tables');
  const unknown = Object.entries(SLICE_TABLES).flatMap(([slice, tables]) =>
    tables.filter((table) => !SCHEMA_TABLES.has(table)).map((table) => `${slice}: ${table}`));
  assert.deepEqual(unknown, [], 'a misspelt table name is a slice that never refreshes');
});

test('every slice has a loader and every loader a slice', () => {
  const block = loaders.slice(loaders.indexOf('export const SLICE_LOADERS'));
  const loaderNames = [...block.matchAll(/^ {2}async (\w+)\(\)/gm)].map((m) => m[1]).sort();
  assert.ok(loaderNames.length > 5, 'control: no loaders were read');
  assert.deepEqual(loaderNames, Object.keys(SLICE_TABLES).sort());
});

test('liveness slices are slices', () => {
  for (const slice of LIVENESS_SLICES) assert.ok(SLICE_TABLES[slice], slice);
});

test('slicesReading keeps map order and says no for a table nothing reads', () => {
  const readers = Object.keys(SLICE_TABLES).filter((s) => SLICE_TABLES[s].includes('settings'));
  assert.ok(readers.length > 1, 'control: settings is read by more than one slice');
  assert.deepEqual(slicesReading(['settings']), readers);
  assert.deepEqual(slicesReading(['no_such_table']), []);
  assert.deepEqual(slicesReading([]), []);
  assert.deepEqual(slicesReading(['agents'], ['files']), [], 'the restriction to a subset was ignored');
});
