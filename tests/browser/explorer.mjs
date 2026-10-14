import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { chromium, firefox } from 'playwright';

const baseUrl = process.env.LADIES_BROWSER_TEST_URL || 'http://127.0.0.1:8765';
const resultDir = process.env.LADIES_BROWSER_RESULT_DIR || 'test-results/browser';
const engineName = process.env.LADIES_BROWSER_ENGINE || 'chromium';
const browserType = { chromium, firefox }[engineName];
assert.ok(browserType, `Unsupported browser engine: ${engineName}`);
await fs.mkdir(resultDir, { recursive: true });

const svg = encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900"><rect width="600" height="900" fill="#f2d9e6"/><circle cx="300" cy="250" r="120" fill="#cc2d7c" opacity=".22"/><rect x="125" y="430" width="350" height="260" rx="80" fill="#cc2d7c" opacity=".18"/></svg>`);
const image = `data:image/svg+xml,${svg}`;
const now = new Date().toISOString();
const personState = new Map([
  [1, { preference: 'NONE', favorite: false, repeat_interest: 'UNKNOWN', private_note: '' }],
  [2, { preference: 'NONE', favorite: false, repeat_interest: 'UNKNOWN', private_note: '' }],
]);
const stateUpdates = [];

const rows = [
  { advert_id: '1001', person_id: 1, name: 'Anna', age: '24', city: 'Leipzig', thumbnail_url: image, preference: 'NONE', favorite: false, current_status: 'ACTIVE', has_video: false, has_ai_content: false, markers: { studio: false } },
  { advert_id: '1002', person_id: 2, name: 'Bella', age: '27', city: 'Leipzig', thumbnail_url: image, preference: 'NONE', favorite: false, current_status: 'ACTIVE', has_video: true, has_ai_content: true, markers: { studio: true, faceapp: true } },
];

const thread = {
  thread_id: 77,
  person_id: 1,
  display_name: 'Anna',
  unread_count: 1,
  last_message_at: now,
  last_message: { text: 'Bis später', message_type: 'TEXT', timestamp: now },
};

function profileBundle(advertId) {
  const row = rows.find(item => item.advert_id === String(advertId)) || rows[0];
  return {
    identity: {
      person_id: row.person_id,
      display_name: row.name,
      preference: personState.get(row.person_id)?.preference || 'NONE',
      favorite: Boolean(personState.get(row.person_id)?.favorite),
      known_listing_count: row.person_id === 1 ? 2 : 1,
      reappear_count: row.person_id === 1 ? 1 : 0,
      person_first_seen_at: now,
      person_last_seen_at: now,
    },
    listing: { advert_id: row.advert_id, last_seen_at: now, current_url: `https://example.invalid/${row.advert_id}` },
    profile: {
      name: row.name,
      city: row.city,
      description: `${row.name} Profilbeschreibung`,
      traits: { Alter: [String(row.age)], Busen: ['mittel'], Figur: ['schlank'] },
      services: { Service: ['ZK', 'GV'] },
      opening_times: { Heute: ['10:00–22:00'] },
      source_url: `https://example.invalid/${row.advert_id}`,
      media: [{ kind: 'IMAGE', local_url: image, position: 0 }],
    },
  };
}

function personDetail(personId) {
  const row = rows.find(item => item.person_id === Number(personId)) || rows[0];
  return {
    person: { person_id: row.person_id, display_name: row.name, first_seen_at: now, last_seen_at: now, listing_count: row.person_id === 1 ? 2 : 1, reappear_count: row.person_id === 1 ? 1 : 0 },
    user_state: personState.get(row.person_id),
    latest_snapshot: {
      name: row.name,
      city: row.city,
      description: `${row.name} Profilbeschreibung`,
      traits: { Alter: [String(row.age)], Busen: ['mittel'], Figur: ['schlank'] },
      services: { Service: ['ZK', 'GV'] },
      opening_times: { Heute: ['10:00–22:00'] },
      source_url: `https://example.invalid/${row.advert_id}`,
      media: [{ kind: 'IMAGE', local_url: image, position: 0 }],
    },
    listings: [{ listing_id: row.person_id * 10, advert_id: row.advert_id, current_status: 'ACTIVE', current_url: `https://example.invalid/${row.advert_id}`, first_seen_at: now, last_seen_at: now }],
    timeline: [{ change_event_id: row.person_id * 100, person_id: row.person_id, event_type: 'STATUS_CHANGED', field_key: 'status', created_at: now }],
    price_lists: [{ price_list_id: row.person_id * 1000, observed_at: now, source: 'USER_ENTERED', items: [{ label: '60 Minuten', duration_minutes: 60, amount_eur: 150 }] }],
    encounters: [{ encounter_id: row.person_id * 10000, occurred_at: now, duration_minutes: 60, total_price_eur: 150, overall_rating: 4, sympathy_rating: 5, services: [{ service_name: 'ZK', amount_eur: 20, performed: true, note: 'gut' }] }],
    analytics: { media: { analyzed_images: 0, flagged_images: 0, shared_with_other_people: 0, reused_across_listings: 0 }, consistency: { city_count: 1, name_count: 1 }, affinity: { score: null, like_samples: 0, dislike_samples: 0, positive: [], negative: [], note: 'Noch nicht genug Daten' } },
    identifiers: [],
    source_profiles: [],
  };
}

function listItems() {
  return [{
    person_id: 1,
    latest_advert_id: '1001',
    display_name: 'Anna',
    city: 'Leipzig',
    thumbnail_url: image,
    status: 'ACTIVE',
    preference: 'LIKE',
    favorite: true,
    listing_count: 2,
  }];
}

function threadDetail() {
  return {
    thread,
    messages: [
      { message_id: 701, thread_id: 77, direction: 'IN', message_type: 'TEXT', text: 'Hallo aus dem Browser-Smoke-Test', timestamp: now, status: 'READ' },
      { message_id: 702, thread_id: 77, direction: 'OUT', message_type: 'TEXT', text: 'Bis später', timestamp: now, status: 'SENT' },
    ],
    endpoints: [{ endpoint_id: 5, kind: 'WHATSAPP', address: '+491701234567' }],
    selected_endpoint: { endpoint_id: 5, kind: 'WHATSAPP', address: '+491701234567' },
    has_more: false,
    next_before: null,
  };
}

function json(route, value, status = 200) {
  return route.fulfill({ status, contentType: 'application/json; charset=utf-8', body: JSON.stringify(value) });
}

const browser = await browserType.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 430, height: 932 }, deviceScaleFactor: 1 });
const pageErrors = [];
page.on('pageerror', error => pageErrors.push(String(error)));
page.on('console', message => { if (message.type() === 'error') pageErrors.push(`console: ${message.text()}`); });

await page.route('**/api/**', async route => {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;
  const method = request.method();

  if (path === '/api/client-log') return json(route, {});
  if (path === '/api/config') return json(route, {});
  if (path === '/api/settings') return json(route, { settings: { visual_check_enabled: false, replay_likes: true, autoplay_videos: false, reduced_motion: true, archive_videos: false, archive_raw_html: true, show_description_changes: false, default_filters: null } });
  if (path === '/api/storage') return json(route, { image_count: 2, video_count: 1, visual_analysis_count: 0, total_bytes: 4096, database_bytes: 2048, media_bytes: 2048, raw_html_bytes: 0 });
  if (path === '/api/dashboard') return json(route, { counts: { persons: 2, people_with_relistings: 1, media_assets: 3, changes: 2 }, usage: { searches: 1, decisions: 0, like_rate: 0, avg_results: 2 }, recent_changes: [] });
  if (path === '/api/recent-searches') return json(route, { items: [] });
  if (path === '/api/pricing/runtime/status') return json(route, { detail: 'Test Runtime', extractor: { ready: true } });
  if (path === '/api/pricing/settings') return json(route, { settings: { price_intelligence_enabled: true, price_magic_button_enabled: true, price_ocr_enabled: true, price_semantic_model_enabled: false } });
  if (path === '/api/messaging/status') return json(route, { state: 'CONNECTED', typing_enabled: true });
  if (path === '/api/messaging/threads') return json(route, { items: [thread] });
  if (path === '/api/messaging/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'data: {}\n\n' });
  if (path === '/api/search-preview') return json(route, { count: 2 });
  if (path === '/api/search') return json(route, { results: rows, meta: { total: 2 } });
  if (/^\/api\/lists\/[^/]+$/.test(path)) return json(route, { items: listItems() });
  if (/^\/api\/profile\/[^/]+$/.test(path)) return json(route, profileBundle(decodeURIComponent(path.split('/').at(-1))));
  if (/^\/api\/person\/\d+\/state$/.test(path) && method === 'POST') {
    const personId = Number(path.split('/')[3]);
    const patch = request.postDataJSON() || {};
    const next = { ...(personState.get(personId) || {}), ...patch };
    personState.set(personId, next);
    stateUpdates.push({ personId, patch });
    const row = rows.find(item => item.person_id === personId);
    if (row) { if ('preference' in patch) row.preference = patch.preference; if ('favorite' in patch) row.favorite = patch.favorite; }
    return json(route, { user_state: next });
  }
  if (/^\/api\/person\/\d+$/.test(path)) return json(route, personDetail(Number(path.split('/').at(-1))));
  if (path === '/api/messaging/threads/77' && method === 'GET') return json(route, threadDetail());
  if (path === '/api/messaging/threads/77/read' && method === 'POST') return json(route, {});
  if (path === '/api/pricing/threads/77/signal') return json(route, { signal: null });
  if (path === '/api/pricing/threads/77/timeline') return json(route, { items: [] });
  if (/^\/api\/pricing\/person\/\d+\/current$/.test(path)) return json(route, { person_id: 1, price_list_id: 1000, proposal: { offers: [{ label: '60 Minuten', duration_minutes: 60, amount_cents: 15000 }], service_rules: [], conditions: [] } });
  if (/^\/api\/pricing\/person\/\d+\/history$/.test(path)) return json(route, { items: [{ price_list_id: 1000, observed_at: now, source: 'CHAT_CONFIRMED', revision: { revision_type: 'FULL' }, proposal: { offers: [{ label: '60 Minuten', duration_minutes: 60, amount_cents: 15000 }], service_rules: [], conditions: [] } }] });
  return json(route, {});
});

try {
  await page.goto(baseUrl, { waitUntil: 'networkidle' });
  await page.waitForSelector('.search-screen');
  await page.waitForFunction(() => document.body.innerText.includes('2 Treffer'));

  const visual = await page.evaluate(() => {
    const rect = selector => {
      const r = document.querySelector(selector).getBoundingClientRect();
      return { width: Math.round(r.width), height: Math.round(r.height), top: Math.round(r.top), bottom: Math.round(r.bottom) };
    };
    const root = getComputedStyle(document.documentElement);
    return {
      accent: root.getPropertyValue('--color-accent').trim(),
      phone: rect('#app'),
      status: rect('.statusbar'),
      header: rect('#appHeader'),
      nav: rect('#bottomNav'),
      footer: rect('.search-footer'),
    };
  });
  assert.equal(visual.accent, '#cc2d7c');
  assert.deepEqual(visual.phone, { width: 430, height: 932, top: 0, bottom: 932 });
  assert.equal(visual.status.height, 30);
  assert.equal(visual.header.height, 64);
  assert.equal(visual.nav.height, 76);
  assert.ok(visual.footer.bottom <= visual.nav.top + 1, 'Search CTA must remain above bottom navigation');
  await page.screenshot({ path: `${resultDir}/v13-search-reference-${engineName}.png`, fullPage: true });

  await page.click('[data-action="search-submit"]');
  await page.waitForSelector('.swipe-screen');
  await page.waitForFunction(() => document.body.innerText.includes('Anna'));
  await page.waitForSelector('.profile-details');

  const scrollResult = await page.evaluate(() => {
    const scroller = document.querySelector('[data-swipe-scroll]');
    const details = document.querySelector('.profile-details');
    const before = scroller.scrollTop;
    scroller.scrollTop = scroller.scrollHeight;
    return { before, after: scroller.scrollTop, detailsTop: details.getBoundingClientRect().top };
  });
  assert.ok(scrollResult.after > scrollResult.before, 'Up-scroll must reveal profile details');

  const surface = page.locator('[data-profile-media]').first();
  const box = await surface.boundingBox();
  assert.ok(box, 'Swipe media surface must exist');
  await page.mouse.move(box.x + box.width * 0.25, box.y + box.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.8, box.y + box.height * 0.5, { steps: 6 });
  await page.mouse.up();
  await page.waitForFunction(() => document.body.innerText.includes('Bella'));
  assert.ok(stateUpdates.some(update => update.personId === 1 && update.patch.preference === 'LIKE'), 'Right swipe must persist LIKE');

  await page.click('[data-route="grid"]');
  await page.waitForSelector('.grid-card');
  assert.equal(await page.locator('.grid-card').count(), 2);
  assert.ok((await page.locator('.grid-card').nth(1).innerText()).includes('KI'));
  await page.locator('.grid-card').first().locator('.grid-card-copy').click();
  await page.waitForSelector('.standalone-profile');
  await page.waitForFunction(() => document.body.innerText.includes('Profilbeschreibung'));

  await page.click('[data-action="profile-more"]');
  await page.waitForSelector('[data-action="person-pricing"]');
  await page.click('[data-action="person-pricing"]');
  await page.waitForFunction(() => document.body.innerText.includes('Aktuelle bestätigte Preise'));
  assert.ok((await page.locator('.bottom-sheet').innerText()).includes('60 Minuten'));
  await page.keyboard.press('Escape');
  await page.click('.profile-back-button');
  await page.waitForSelector('.grid-screen');

  await page.click('[data-route="favorites"]');
  await page.waitForSelector('.favorites-screen');
  await page.waitForSelector('.favorite-card');
  assert.ok((await page.locator('.favorite-card').first().innerText()).includes('Anna'));

  await page.click('[data-route="messages"]');
  await page.waitForSelector('.messages-screen');
  await page.waitForSelector('.conversation-row');
  assert.ok((await page.locator('.messages-status').innerText()).includes('Verbunden'));
  await page.locator('.conversation-row').first().click();
  await page.waitForSelector('.chat-screen');
  await page.waitForFunction(() => document.body.innerText.includes('Hallo aus dem Browser-Smoke-Test'));
  assert.equal(await page.locator('.bubble-row').count(), 2);
  await page.click('#appHeader [data-action="back"]');
  await page.waitForSelector('.messages-screen');

  await page.click('[data-route="settings"]');
  await page.waitForSelector('.settings-screen');
  const settingsText = await page.locator('.settings-screen').innerText();
  assert.ok(settingsText.includes('WhatsApp'));
  assert.ok(settingsText.includes('Speicher'));

  assert.deepEqual(pageErrors, [], `Browser console/page errors: ${pageErrors.join(' | ')}`);
  console.log(`V13 browser smoke passed in ${engineName}.`);
} finally {
  await browser.close();
}
