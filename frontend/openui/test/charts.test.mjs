import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
import { ringSegments, dayColumns } from '../src/chart-geometry.mjs';
import { displayMoney, currencyName } from '../src/money-display.mjs';

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

let scratch, render, categoryIconName, categoryArtwork;
before(async () => {
  scratch = await mkdtemp(path.join(tmpdir(), 'ledger-chart-ssr-'));
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const outfile = path.join(scratch, 'render.cjs');
  await build({ stdin: { contents: `
    import React from 'react';
    import { renderToStaticMarkup } from 'react-dom/server';
    import { RingChart, DayChart, Icon } from './src/charts.jsx';
    import { CategoryLabel, categoryIconName, categoryArtwork } from './src/category-icons.jsx';
    export { categoryIconName, categoryArtwork };
    export const render = (name, props) => renderToStaticMarkup(React.createElement({RingChart, DayChart, Icon, CategoryLabel}[name], props));
  `, resolveDir: root, loader: 'jsx' }, bundle: true, platform: 'node', format: 'cjs', outfile,
    define: { 'process.env.NODE_ENV': '"production"' } });
  ({ render, categoryIconName, categoryArtwork } = createRequire(import.meta.url)(outfile));
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


// Read the domain defaults so a newly added built-in category cannot silently
// regress to the generic icon. No dependency on ledger data or personal habits.
test('every built-in category and parent has explicit artwork', async () => {
  const vocabulary = await readFile(new URL('../../../scripts/ledger_core/domain/vocabulary.py', import.meta.url), 'utf8');
  const metrics = await readFile(new URL('../../../scripts/ledger_core/domain/metrics.py', import.meta.url), 'utf8');
  const keywords = vocabulary.split('CATEGORY_KEYWORDS = {')[1].split('\n}')[0];
  const parents = metrics.split('DEFAULT_CATEGORY_PARENTS = {')[1].split('\n}')[0];
  const names = new Set([...keywords.matchAll(/^    "([^"]+)":/gm)].map(match => match[1]));
  for (const match of parents.matchAll(/"([^"]+)"/g)) names.add(match[1]);
  names.add('红包收入');
  for (const name of names) {
    const icon = categoryIconName(name);
    assert.notEqual(icon, 'tag', name);
    assert.ok(categoryArtwork[icon], name);
  }
});

test('category labels escape custom names and use an accessible local fallback', () => {
  for (const name of ['自定义项目', '__proto__', 'constructor', '<script>bad()</script>', null]) {
    assert.equal(categoryIconName(name), 'tag');
    const html = render('CategoryLabel', { category: name });
    assert.match(html, /aria-hidden="true"/);
    assert.match(html, /data-category-icon="tag"/);
    assert.doesNotMatch(html, /<script>|<img|https?:|tabindex/);
  }
  const html = render('CategoryLabel', { category: '咖啡茶饮' });
  assert.match(html, /咖啡茶饮/);
  assert.match(html, /data-category-icon="coffee"/);
});

test('category chart icons do not mislabel merchant breakdowns as categories', () => {
  const props = { rows: [{ label: '餐饮', amount: '12.35' }], currency: 'CNY' };
  assert.match(render('RingChart', { ...props, categories: true }), /data-category-icon="meal"/);
  assert.doesNotMatch(render('RingChart', props), /data-category-icon/);
});

test('currency symbols preserve exact decimals and signed values', () => {
  assert.equal(displayMoney('8000.00', 'CNY'), '¥8000.00');
  assert.equal(displayMoney('-12.30', 'CNY'), '-¥12.30');
  assert.equal(displayMoney('9007199254740993.01', 'USD'), '$9007199254740993.01');
  assert.equal(displayMoney('0.00', 'EUR'), '€0.00');
  assert.equal(displayMoney('12.000', 'JPY'), '¥12.000');
  assert.equal(displayMoney('2.50', 'INVALID'), '¤2.50');
  assert.equal(displayMoney(null, 'CNY'), '—');
  assert.equal(currencyName('CNY'), '人民币');
});

test('chart monetary labels and date options use symbols without currency codes', () => {
  const rows = [{ label: '2026-10-01', amount: '12.35' }];
  for (const component of ['RingChart', 'DayChart']) {
    const html = render(component, { rows, currency: 'CNY', period: { start: '2026-10-01', end: '2026-10-31' } });
    assert.match(html, /¥12\.35/);
    assert.doesNotMatch(html, /CNY/);
  }
});
