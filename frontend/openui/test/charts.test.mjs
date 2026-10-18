import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
import { ringSegments, dayColumns } from '../src/chart-geometry.mjs';

test('ring geometry preserves source values and closes exactly one circle', () => {
  const rows = [{ label: '餐饮', amount: '12.35' }, { label: '交通', amount: '7.65' }];
  const rings = ringSegments(rows);
  assert.equal(rings[0].amount, '12.35');
  assert.equal(rings[0].length + rings[1].length, 100);
  assert.equal(rings[1].offset, rings[0].length);
  assert.deepEqual(rows.map(r => r.amount), ['12.35', '7.65']);
});

test('empty, zero and single-category rings avoid invalid SVG geometry', () => {
  assert.deepEqual(ringSegments([]), []);
  assert.equal(ringSegments([{ amount: '0.00' }])[0].length, 0);
  assert.equal(ringSegments([{ amount: '0.01' }])[0].length, 100);
  assert.throws(() => ringSegments([{ amount: 'invalid' }]));
  assert.throws(() => ringSegments([{ amount: '-1.00' }]));
});

test('daily columns retain calendar gaps and exact displayed amounts', () => {
  const rows = [{ label: '2026-10-01', amount: '10.00' }, { label: '2026-10-04', amount: '20.00' }];
  const columns = dayColumns(rows, '2026-10-01', '2026-10-05');
  assert.equal(columns.length, 2); // missing days are never synthesized as zeros
  assert.equal(columns[1].x - columns[0].x, 210);
  assert.equal(columns[1].height / columns[0].height, 2);
  assert.equal(columns[0].amount, '10.00');
  assert.throws(() => dayColumns(rows, '2026-10-02', '2026-10-05'));
  assert.throws(() => dayColumns(rows, '2026-10-05', '2026-10-01'));
});

let scratch, render;
before(async () => {
  scratch = await mkdtemp(path.join(tmpdir(), 'ledger-chart-ssr-'));
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const outfile = path.join(scratch, 'render.cjs');
  await build({ stdin: { contents: `
    import React from 'react';
    import { renderToStaticMarkup } from 'react-dom/server';
    import { RingChart, DayChart, Icon } from './src/charts.jsx';
    export const render = (name, props) => renderToStaticMarkup(React.createElement({RingChart, DayChart, Icon}[name], props));
  `, resolveDir: root, loader: 'jsx' }, bundle: true, platform: 'node', format: 'cjs', outfile,
    define: { 'process.env.NODE_ENV': '"production"' } });
  render = createRequire(import.meta.url)(outfile).render;
});
after(async () => { if (scratch) await rm(scratch, { recursive: true, force: true }); });

test('ring rendering provides labeled keyboard buttons and escapes source text', () => {
  const html = render('RingChart', { rows: [{ label: '<script>bad()</script>', amount: '12.35' }], currency: 'CNY' });
  assert.match(html, /<svg/);
  assert.match(html, /<button[^>]*aria-pressed="false"/);
  assert.match(html, /12\.35/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});

test('daily chart exposes date selection and missing-data explanation', () => {
  const html = render('DayChart', { rows: [{ label: '2026-10-01', amount: '9.99' }], currency: 'CNY', period: { start: '2026-10-01', end: '2026-10-31' } });
  assert.match(html, /<select/);
  assert.match(html, /9\.99/);
  assert.match(html, /空白日期表示没有记录/);
  assert.match(html, /role="img"/);
});

