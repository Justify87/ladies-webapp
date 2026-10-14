import assert from 'node:assert/strict';
import { chromium, firefox } from 'playwright';

const baseUrl = process.env.LADIES_BROWSER_TEST_URL || 'http://127.0.0.1:8765';
const engineName = process.env.LADIES_BROWSER_ENGINE || 'chromium';
const browserType = { chromium, firefox }[engineName];
assert.ok(browserType, `Unsupported browser engine: ${engineName}`);

const browser = await browserType.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 430, height: 932 } });
const diagnostics = [];

page.on('pageerror', error => {
  const line = `pageerror: ${error}`;
  diagnostics.push(line);
  console.error(line);
});
page.on('console', message => {
  if (message.type() !== 'error') return;
  const line = `console: ${message.text()}`;
  diagnostics.push(line);
  console.error(line);
});
page.on('requestfailed', request => {
  const line = `requestfailed: ${request.method()} ${request.url()} ${request.failure()?.errorText || ''}`;
  diagnostics.push(line);
  console.error(line);
});
page.on('response', response => {
  if (response.status() < 400) return;
  const line = `response: ${response.status()} ${response.url()}`;
  diagnostics.push(line);
  console.error(line);
});

await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname;
  if (path === '/api/messaging/events') {
    return route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'data: {}\n\n' });
  }
  return route.fulfill({ status: 200, contentType: 'application/json; charset=utf-8', body: '{}' });
});

try {
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  const snapshot = await page.evaluate(() => ({
    href: location.href,
    title: document.title,
    bodyText: document.body?.innerText || '',
    bodyHtml: document.body?.innerHTML || '',
    bootstrapReady: Boolean(window.__LADIES_EXPLORER_V13__),
    searchCount: document.querySelectorAll('.search-screen').length,
    moduleScripts: [...document.querySelectorAll('script[type="module"]')].map(script => script.src),
  }));
  console.log(JSON.stringify({ ...snapshot, bodyHtml: snapshot.bodyHtml.slice(0, 4000), diagnostics }, null, 2));
  assert.ok(snapshot.searchCount > 0, 'V13 bootstrap did not render .search-screen');
  assert.equal(snapshot.bootstrapReady, true, 'V13 bootstrap marker was not installed');
} finally {
  await browser.close();
}
