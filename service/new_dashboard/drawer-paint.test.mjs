// The agent drawer is repainted on every data refresh; these pin what makes that invisible.

import assert from 'node:assert/strict';
import test from 'node:test';

import { paintAgentDrawer, paintIfChanged } from './drawer-paint.mjs';

/** Just enough element: innerHTML replaces the children, and panels are found by id. */
function fakeElement() {
  const el = {
    writes: 0,
    _html: '',
    firstElementChild: null,
    panels: new Map(),
    get innerHTML() { return this._html; },
    set innerHTML(value) {
      this.writes += 1;
      this._html = value;
      this.firstElementChild = value ? { token: Symbol('root') } : null;
      this.panels = new Map();
      for (const match of value.matchAll(/id="([^"]+)">([^<]*)</g)) {
        const panel = fakeElement();
        panel._html = match[2];
        this.panels.set(match[1], panel);
      }
    },
    querySelector(selector) { return this.panels.get(selector.slice(1)) || null; },
  };
  return el;
}

const drawer = (status) => `<div class="agent-drawer">${status}<div id="procs">Reading this agent's terminals…</div></div>`;

test('an identical re-render of the same agent writes nothing', () => {
  const host = fakeElement();
  assert.equal(paintAgentDrawer(host, 'a1', drawer('online'), 'procs'), true);
  assert.equal(paintAgentDrawer(host, 'a1', drawer('online'), 'procs'), false, 'an unchanged refresh rewrote the drawer');
  assert.equal(host.writes, 1);
});

test('a changed render keeps the processes panel instead of resetting it to the placeholder', () => {
  const host = fakeElement();
  paintAgentDrawer(host, 'a1', drawer('online'), 'procs');
  host.querySelector('#procs').innerHTML = '1 terminal(s), 1 live.';
  assert.equal(paintAgentDrawer(host, 'a1', drawer('working'), 'procs'), true);
  assert.equal(host.querySelector('#procs').innerHTML, '1 terminal(s), 1 live.', 'the panel blinked back to its placeholder');
});

test('another agent, or another drawer written in between, paints in full', () => {
  const host = fakeElement();
  paintAgentDrawer(host, 'a1', drawer('online'), 'procs');
  host.querySelector('#procs').innerHTML = 'a1 terminals';
  assert.equal(paintAgentDrawer(host, 'a2', drawer('online'), 'procs'), true);
  assert.notEqual(host.querySelector('#procs').innerHTML, 'a1 terminals', "a1's terminals were carried into a2's drawer");
  host.innerHTML = '<div class="run">a run drawer</div>';
  assert.equal(paintAgentDrawer(host, 'a2', drawer('online'), 'procs'), true, 'a run drawer stayed up under an identical agent render');
});

test('a panel is written only when its content changes', () => {
  const panel = fakeElement();
  assert.equal(paintIfChanged(panel, '<p>x</p>'), true);
  assert.equal(paintIfChanged(panel, '<p>x</p>'), false);
  assert.equal(paintIfChanged(panel, '<p>y</p>'), true);
  panel.innerHTML = '<p>something else wrote here</p>';
  assert.equal(paintIfChanged(panel, '<p>y</p>'), true, 'a foreign write was not repaired');
  assert.equal(paintIfChanged(null, 'x'), false);
});

test('a render that differs only in a relative-time label writes nothing: the ticker owns that label', () => {
  // The drawer carries "last seen Ns ago". Comparing the rendered label made every refresh of a
  // recently seen agent a "change", so the memo that exists to stop repaints almost never held.
  const host = fakeElement();
  const seen = (label) => `<div class="agent-drawer"><span class="rel-time" data-rel-ts="1700000000000">${label}</span><div id="procs">x</div></div>`;
  assert.equal(paintAgentDrawer(host, 'a1', seen('5s'), 'procs'), true);
  assert.equal(paintAgentDrawer(host, 'a1', seen('6s'), 'procs'), false, 'a ticking label repainted the drawer');
  const moved = seen('6s').replace('1700000000000', '1700000009000');
  assert.equal(paintAgentDrawer(host, 'a1', moved, 'procs'), true, 'CONTROL: a new instant is a real change');
});
