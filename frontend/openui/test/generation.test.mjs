import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
let scratch, validateLang, render, library;
before(async () => {
  scratch = await mkdtemp(path.join(tmpdir(), 'ledger-generation-'));
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const outfile = path.join(scratch, 'test.cjs');
  await build({ stdin: { contents: `
    import React from 'react';
    import { renderToStaticMarkup } from 'react-dom/server';
    import { App } from './src/view.jsx';
    export { validateLang, library } from './src/view.jsx';
    export const render = view => renderToStaticMarkup(<App view={view} />);
  `, loader: 'jsx', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', outfile,
    define: { 'process.env.NODE_ENV': '"production"' } });
  ({ validateLang, library, render } = createRequire(import.meta.url)(outfile));
});
after(async () => { if (scratch) await rm(scratch, { force: true, recursive: true }); });

test('different agent-authored page structures share authoritative data references', () => {
  const programs = [
    'root = LedgerView([Section("预算", [Budget("budgets"), Text("关注已记录支出")], "amber")])',
    'root = LedgerView([Columns([Breakdown("by_day"), Breakdown("by_category")], 2), Disclosure("核对依据", [Transactions("transactions")])])',
    'root = LedgerView([Tabs(["概览", "明细"], [Stack([Metric("totals.expense"), Text("仅包含已记录数据")]), Transactions("transactions")])])',
  ];
  assert.deepEqual(validateLang(programs[0], 'report').refs, ['budgets']);
  assert.deepEqual(validateLang(programs[1], 'report').refs, ['by_day', 'by_category', 'transactions']);
  assert.deepEqual(validateLang(programs[2], 'report').refs, ['totals.expense', 'transactions']);
});

test('malformed graphs and forbidden capabilities return actionable errors', () => {
  for (const code of [
    'root = LedgerView([unknown])',
    'root = LedgerView([Metric("totals.expense", 999)])',
    'root = LedgerView([ImportRows("import_rows")])',
    'root = LedgerView([Columns([Text("x")], 99)])',
    'root = LedgerView([Tabs(["one"], [Text("x"), Text("y")])])',
    'root = LedgerView([Text("x")])\nq = Query("read", {})',
    'root = LedgerView([Text("x")])\nm = Mutation("write", {})',
    'root = LedgerView([Text("x")])\norphan = Text("unused")',
    'root = LedgerView(["not a component"])',
    'root = LedgerView([])',
    'root = LedgerView([Text("x", "invalid-color")])',
  ]) assert.throws(() => validateLang(code, 'report'), undefined, code);
});

test('rendering an authored view preserves core amounts and escapes prose', () => {
  const lang = 'root = LedgerView([Section("本月", [Columns([Metric("totals.expense"), Text("<script>bad()</script>")], 2)], "violet")])';
  validateLang(lang, 'report');
  const html = render({ mode: 'openui', kind: 'report', lang, period: { label: '本月' }, currency: 'CNY', revision: 1,
    coverage: { notice: '仅已记录数据', recorded_rows: 2, unassigned_rows: 0, unknown_openings: 0 },
    datasets: { 'totals.expense': { value: '22.00', label: '支出', evidence: ['a', 'b'] } } });
  assert.match(html, /22\.00/);
  assert.match(html, /generated-columns/);
  assert.match(html, /&lt;script&gt;bad\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>bad/);
});

test('generated HTML stays inside an opaque sandbox and carries no write bridge', () => {
  const html = render({ mode: 'html', html_document: '<!doctype html><button>demo</button>', kind: 'report',
    period: { label: '本月' }, currency: 'CNY', coverage: { notice: '部分记录' } });
  assert.match(html, /sandbox="allow-scripts"/);
  assert.match(html, /srcDoc="&lt;!doctype html&gt;/);
  assert.doesNotMatch(html, /allow-same-origin|allow-top-navigation|allow-popups/);
});

test('income composition exposes income charts and details with accurate labels', () => {
  const lang = 'root = LedgerView([Breakdown("by_income_merchant"), Breakdown("by_income_day"), Transactions("income_transactions"), Transactions("transactions")])';
  assert.deepEqual(validateLang(lang, 'report').refs, ['by_income_merchant', 'by_income_day', 'income_transactions', 'transactions']);
  const row = { id: 'salary', occurred_at: '2026-10-01', type: 'income', merchant: '公司', category: '工资', amount: '8000.00', currency: 'CNY' };
  const html = render({ mode: 'openui', kind: 'report', lang, currency: 'CNY', revision: 1,
    period: { label: '本月', start: '2026-10-01', end: '2026-10-31' }, coverage: { notice: '仅已记录数据' },
    datasets: { by_income_merchant: { label: '收入来源', rows: [{ label: '公司', amount: '8000.00' }] },
      by_income_day: { label: '每日已记录收入', rows: [{ label: '2026-10-01', amount: '8000.00' }] },
      income_transactions: [row], transactions: [row] } });
  assert.match(html, /收入来源占比/);
  assert.match(html, /每日已记录收入/);
  assert.match(html, /收入明细/);
  assert.match(html, /收支类型/);
  assert.match(html, /8000\.00/);
  assert.doesNotMatch(html, /零支出|支出分类/);
});

test('packaged generation spec comes from this exact component library', async () => {
  const bundled = JSON.parse(await readFile(new URL('../../../assets/openui/generation.json', import.meta.url)));
  assert.deepEqual(bundled.library, JSON.parse(JSON.stringify(library.toSpec())));
  assert.equal(bundled.version, 2);
  assert.deepEqual(bundled.library.schema, library.toJSONSchema());
  assert.match(bundled.prompt, /Columns/);
});
