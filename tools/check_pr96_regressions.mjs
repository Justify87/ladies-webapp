import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const core=readFileSync(new URL('../ladies/static/explorer/js/effects/core.js',import.meta.url),'utf8');
const map=readFileSync(new URL('../ladies/static/explorer/js/effects/map.js',import.meta.url),'utf8');
const discover=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/discover.js',import.meta.url),'utf8');
const favorites=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/favorites.js',import.meta.url),'utf8');

assert.match(core,/SWIPE_READY_TIMEOUT_MS=\d+/,'swipe readiness must have a bounded deadline');
assert.match(core,/MEDIA_WARM_TIMEOUT_MS=\d+/,'media warming must have a bounded deadline');
assert.match(core,/async function prefetchCurrent\(\{waitForPrimary=false\}=\{\}\)[\s\S]*if\(!waitForPrimary\)return \[\]/,'normal swipe prefetch must not block navigation');
assert.match(core,/performSearch\(\)[\s\S]*prefetchCurrent\(\{waitForPrimary:true\}\)[\s\S]*router\.replaceRoot\('discover'\)/,'fresh searches must prime the current swipe profile before exposing Discover');
assert.match(core,/const finish=ok=>\{\s*if\(ok\)warmedMedia\.add\(url\)/,'media URLs must only be marked warm after a successful load');
assert.doesNotMatch(core,/function warmImage[\s\S]{0,220}warmedMedia\.add\(url\);\s*return new Promise/,'failed media must remain retryable');

assert.match(map,/let retryKey=null/,'map retry budget must track the active result key');
assert.match(map,/if\(retryKey!==requested\.key\)resetRetryBudget\(requested\.key\)/,'a new result set must receive a fresh map retry budget');
assert.match(map,/resetRetryBudget\(null\)/,'cancelling map work must clear retry ownership');

assert.match(core,/succeeded=0,failed=0/,'bulk status refresh must count successes and failures separately');
assert.match(core,/patch:\{done:i\+1,succeeded,failed\}/,'bulk status progress must publish real per-attempt outcomes');
assert.match(favorites,/s\.succeeded/,'favorites progress UI must expose successful checks');
assert.match(favorites,/s\.failed/,'favorites progress UI must expose failed checks');

assert.match(discover,/Profil wird vorbereitet/,'an unprimed swipe profile must render a loading state instead of a false no-image fallback');
