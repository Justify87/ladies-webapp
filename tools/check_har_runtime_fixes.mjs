import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pricingExtractionAvailable, pricingManualAvailable } from '../ladies/static/explorer/js/app/selectors.js';

const discoverSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/discover.js',import.meta.url),'utf8');
const messagingSource=readFileSync(new URL('../ladies/static/explorer/js/effects/messaging.js',import.meta.url),'utf8');
const messagingService=readFileSync(new URL('../ladies/messaging/service.py',import.meta.url),'utf8');
const pricingSource=readFileSync(new URL('../ladies/static/explorer/js/effects/pricing.js',import.meta.url),'utf8');
const chromeSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/chrome.js',import.meta.url),'utf8');
const configSource=readFileSync(new URL('../ladies/config.py',import.meta.url),'utf8');
const indexSource=readFileSync(new URL('../ladies/templates/index.html',import.meta.url),'utf8');

// Classified backend views own thread ordering. The browser may merge product
// collections, but must not introduce favorite/like ordering over message recency.
assert.match(messagingService,/order_by\(desc\(ChatThread\.last_message_at\), desc\(ChatThread\.updated_at\)\)/);
assert.doesNotMatch(messagingService,/out\.sort\(key=lambda item:/,'favorite/like ordering must not override message recency');
assert.match(messagingSource,/api\.messagingInbox\('ALL'\)/,'Ladies inbox must come from the classified backend view');
assert.match(messagingSource,/api\.messagingReview\(\)/,'review queue must come from the classified backend view');
assert.match(messagingSource,/type:'THREADS',items:\[\.\.\.merged\.values\(\)\]/,'classified views are merged without client-side business sorting');
assert.doesNotMatch(messagingSource,/newestThreads|threadTime|\.sort\(/,'frontend must not duplicate canonical thread ordering');

// One shared pricing capability definition drives both actions and UI.
const pricingState=(enabled,magic,ready)=>({settings:{pricing:{price_intelligence_enabled:enabled,price_magic_button_enabled:magic},pricingRuntime:{extractor:{ready}}}});
assert.equal(pricingExtractionAvailable(pricingState(true,true,true)),true);
assert.equal(pricingExtractionAvailable(pricingState(false,true,true)),false);
assert.equal(pricingExtractionAvailable(pricingState(true,true,false)),false);
assert.equal(pricingManualAvailable(pricingState(true,true,true)),true);
assert.equal(pricingManualAvailable(pricingState(true,false,true)),false);
assert.match(pricingSource,/ensureExtractionAvailable/);
assert.match(pricingSource,/S\.pricingExtractionAvailable\(state\(\)\)/,'automatic signal loading must use the shared capability');
assert.match(chromeSource,/S\.pricingManualAvailable\(state\)/,'manual pricing UI must use the shared capability');
assert.doesNotMatch(pricingSource,/Promise\.all\(\[api\.pricingSettings\(\),api\.pricingStatus/,'pricing boot must not fetch runtime status twice');

assert.match(discoverSource,/loading="lazy" decoding="async"/,'Grid thumbnails must not all load eagerly');
assert.match(configSource,/FILTER_UI_LABELS/,'ambiguous parser aliases need explicit UI labels');
assert.match(configSource,/"ab18": "Mit 18\+ Inhalten"/);
assert.match(configSource,/"girlfriendsex": "Girlfriend-Sex"/);
assert.doesNotMatch(configSource,/len\(text\.strip\(\)\)/,'UI labels must not be selected by string-length heuristics');
assert.match(indexSource,/rel="icon" href="\/static\/explorer\/favicon\.svg"/);

console.log('HAR runtime regression checks passed');
