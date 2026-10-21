import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../../../', import.meta.url));
const client = execFileSync('python3', ['-c', 'import sys;sys.path.insert(0,"scripts");from ledger_core.services.preview import CLIENT;print(CLIENT)'], { cwd: root, encoding: 'utf8' });

function harness() {
  const status = {textContent: ''};
  const frames = [];
  const polls = [];
  let state = {state: 'ready', version: 1};
  let offline = false;
  const events = {};
  const context = {
    document: {
      getElementById: id => id === 'status' ? status : {
        querySelectorAll: () => [...frames],
        appendChild: frame => {frames.push(frame); queueMicrotask(() => frame.onload());},
      },
      createElement: () => ({attributes: {}, setAttribute(k, v) {this.attributes[k] = v;}, remove() {frames.splice(frames.indexOf(this), 1);}}),
    },
    window: {addEventListener: (name, callback) => {events[name] = callback;}},
    fetch: async () => {if (offline) throw Error('offline'); return {ok: true, json: async () => state};},
    setTimeout: (callback, ms) => {if (ms === 1000) polls.push(callback); return callback;},
    clearTimeout: () => {},
  };
  vm.runInNewContext(client, context);
  const settle = async () => {for (let i = 0; i < 10; i++) await Promise.resolve();};
  return {frames, status, events, settle, setState: v => {state = v;}, disconnect: () => {offline = true;},
    next: async () => {polls.shift()(); await settle();}};
}

test('preview client keeps the last page on invalid source and swaps only on new valid versions', async () => {
  const h = harness(); await h.settle();
  assert.equal(h.frames.length, 1);
  const first = h.frames[0];
  assert.equal(first.attributes.sandbox, 'allow-scripts');
  assert.equal(first.src, 'view?v=1');
  h.setState({state: 'invalid', version: 1}); await h.next();
  assert.equal(h.frames[0], first);
  assert.match(h.status.textContent, /保留上一版/);
  h.setState({state: 'ready', version: 2}); await h.next();
  assert.equal(h.frames.length, 1);
  assert.notEqual(h.frames[0], first);
  assert.equal(h.frames[0].src, 'view?v=2');
});

test('connection loss preserves the current page and exposes retry status', async () => {
  const h = harness(); await h.settle();
  const first = h.frames[0]; h.disconnect(); await h.next();
  assert.equal(h.frames[0], first);
  assert.match(h.status.textContent, /连接中断/);
  h.events.pagehide();
});
