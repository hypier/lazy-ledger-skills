import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import { chromium } from '@playwright/test';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
let folder, browser;
before(async () => {
  folder = await mkdtemp(path.join(os.tmpdir(), 'ledger-openui-'));
  execFileSync('python3', [path.join(root, 'tests/openui_fixture.py'), folder]);
  browser = await chromium.launch({ channel: process.env.OPENUI_TEST_BROWSER || 'chrome', headless: true });
});
after(async () => { await browser?.close(); if (folder) await rm(folder, { recursive: true, force: true }); });

test('offline renderer shows core amounts, filters evidence and does not execute source HTML', async () => {
  const page = await browser.newPage();
  const requests = [], errors = [];
  page.on('request', r => { if (!r.url().startsWith('file:')) requests.push(r.url()); });
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(pathToFileURL(path.join(folder, 'monthly.html')).href);
  await page.locator('[data-component="Metric"]').first().waitFor();
  assert.match(await page.locator('#app').innerText(), /296\.40/);
  assert.match(await page.locator('#app').innerText(), /7708\.60/);
  assert.match(await page.locator('#app').innerText(), /不代表全部实际收支/);
  await page.getByRole('searchbox').fill('午餐商户 24');
  assert.equal(await page.locator('tbody tr').count(), 1);
  assert.match(await page.locator('tbody').innerText(), /午餐商户 24/);
  await page.getByRole('searchbox').fill('window.injected');
  assert.match(await page.locator('tbody').innerText(), /<script>/);
  assert.equal(await page.evaluate(() => window.injected), undefined);
  assert.equal(await page.locator('#fallback').isVisible(), false);
  await page.getByRole('button', { name: '查看文本快照' }).click();
  assert.equal(await page.locator('#fallback').isVisible(), true);
  assert.deepEqual(requests, []);
  assert.deepEqual(errors, []);
  await page.close();
});

test('budget uses the selected period and core percentage, responsive page fits mobile', async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.goto(pathToFileURL(path.join(folder, 'budget.html')).href);
  await page.locator('[data-component="Budget"]').waitFor();
  assert.match(await page.locator('#app').innerText(), /296\.40/);
  assert.match(await page.locator('#app').innerText(), /59\.3%/);
  assert.match(await page.locator('#app').innerText(), /不足整月/);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (process.env.OPENUI_SCREENSHOT) await page.screenshot({ path: process.env.OPENUI_SCREENSHOT, fullPage: true });
  await page.close();
});

test('import preview distinguishes new, overlapping and failed source rows without any commit UI', async () => {
  const page = await browser.newPage();
  await page.goto(pathToFileURL(path.join(folder, 'import.html')).href);
  await page.locator('[data-component="ImportSummary"]').waitFor();
  assert.match(await page.locator('#app').innerText(), /尚未执行/);
  assert.match(await page.locator('#app').innerText(), /存在未解决事项/);
  await page.getByRole('combobox').selectOption('new');
  assert.equal(await page.locator('tbody tr').count(), 1);
  assert.match(await page.locator('tbody').innerText(), /新商户/);
  assert.equal(await page.getByRole('button', { name: '确认导入', exact: true }).count(), 0);
  await page.close();
});

test('unexpected executable DSL and render errors retain the full text fallback', async () => {
  for (const name of ['broken.html', 'bad-data.html']) {
    const page = await browser.newPage();
    await page.goto(pathToFileURL(path.join(folder, name)).href);
    await page.waitForFunction(() => document.getElementById('status').textContent.includes('不可用'));
    assert.equal(await page.locator('#fallback').isVisible(), true);
    assert.match(await page.locator('#fallback').innerText(), /296\.40/);
    assert.equal(await page.locator('#app').innerText(), '');
    await page.close();
  }
});
