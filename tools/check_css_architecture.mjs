import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const cssRoot=new URL('../ladies/static/explorer/css/',import.meta.url);
const runtimeStyles=[
  'foundation/tokens.css',
  'foundation/base.css',
  'layout/shell.css',
  'layout/navigation.css',
  'components/forms.css',
  'components/filters.css',
  'components/listing-card.css',
  'components/price-list.css',
  'components/overlays.css',
  'features/discover.css',
  'features/search.css',
  'features/saved-searches.css',
  'features/profile.css',
  'features/pricing.css',
  'features/messaging.css',
  'features/settings.css',
  'features/history.css',
  'discover-map.css',
  'favorites-redesign.css',
];
const legacyStyles=[
  'tokens.css',
  'base.css',
  'shell.css',
  'components.css',
  'screens.css',
  'prototype.css',
  'prototype-fixes.css',
  'ux-fixes.css',
  'search-intelligence.css',
  'requested-fixes.css',
  'components/01.css',
  'components/02.css',
  'components/03.css',
  'components/04.css',
  'components/05.css',
  'screens/01a.css',
  'screens/01b.css',
  'screens/02a.css',
  'screens/02b.css',
];

const index=readFileSync(new URL('../ladies/templates/index.html',import.meta.url),'utf8');
const loaded=[...index.matchAll(/href="\/static\/explorer\/css\/([^"?]+\.css)"/g)].map(match=>match[1]);
assert.deepEqual(loaded,runtimeStyles,'index.html must load only the canonical CSS owners in the reviewed order');

for(const path of runtimeStyles){
  assert.equal(existsSync(new URL(path,cssRoot)),true,`runtime stylesheet is missing: ${path}`);
}
for(const path of legacyStyles){
  assert.equal(existsSync(new URL(path,cssRoot)),false,`legacy stylesheet must not return: ${path}`);
}

const sources=new Map(runtimeStyles.map(path=>[path,readFileSync(new URL(path,cssRoot),'utf8')]));
const allCss=[...sources.values()].join('\n');
const tokens=sources.get('foundation/tokens.css');
const base=sources.get('foundation/base.css');
const forms=sources.get('components/forms.css');
const filters=sources.get('components/filters.css');
const listingCard=sources.get('components/listing-card.css');
const priceList=sources.get('components/price-list.css');
const discover=sources.get('features/discover.css');
const search=sources.get('features/search.css');
const savedSearches=sources.get('features/saved-searches.css');
const profile=sources.get('features/profile.css');
const pricing=sources.get('features/pricing.css');
const messaging=sources.get('features/messaging.css');
const settings=sources.get('features/settings.css');
const history=sources.get('features/history.css');

assert.equal((allCss.match(/:root\s*\{/g)||[]).length,1,'design tokens must have exactly one :root owner');
assert.match(tokens,/:root\s*\{/,'foundation/tokens.css must own the design tokens');
assert.doesNotMatch(allCss,/var\(--color-subtle\b/,'retired undefined --color-subtle token must not return');
assert.doesNotMatch(allCss,/Prototype-parity|Production migration additions|Production screen extensions|Production-only V13|Focused follow-up fixes|Central density/,'historical patch-layer comments must not return');

const definedVariables=new Set([...allCss.matchAll(/(--[a-z0-9-]+)\s*:/gi)].map(match=>match[1]));
const usedVariables=new Set([...allCss.matchAll(/var\(\s*(--[a-z0-9-]+)/gi)].map(match=>match[1]));
const runtimeVariables=new Set(['--range-from','--range-to']);
const undefinedVariables=[...usedVariables].filter(name=>!definedVariables.has(name)&&!runtimeVariables.has(name)).sort();
assert.deepEqual(undefinedVariables,[],'every CSS custom property must be defined by CSS or explicitly documented as runtime-provided');

for(const [path,css] of sources){
  if(path==='foundation/base.css'||path==='favorites-redesign.css')continue;
  assert.doesNotMatch(css,/!important\b/,`feature/layout CSS must not rely on !important: ${path}`);
}

for(const [path,css] of sources){
  if(path==='favorites-redesign.css')continue;
  assert.doesNotMatch(css,/\.favorite-card|\.favorites-tabs|\.favorites-toolbar|\.status-check-card/,`legacy Favorites selectors must stay removed: ${path}`);
}

assert.doesNotMatch(discover,/swipe-details|profile-media::after/,'retired Swipe-only presentation must not return');
assert.doesNotMatch(profile,/profile-rail-action|profile-ui-card|keyfacts-grid|analysis-summary|seg-choice/,'retired profile prototype components must not return');
assert.doesNotMatch(messaging,/\.message-tabs\b|\.price-detection\b|\.read-receipts\b|\.messages-heading\b/,'retired messaging generations must not return');
assert.doesNotMatch(search,/\.search-heading\b|\.filter-search-box\b|\.home-recent|\.range-labels\b/,'retired Search presentation must not return');

assert.match(forms,/\.message-search\s*\{/,'shared search control belongs to components/forms.css');
assert.doesNotMatch(messaging,/\.message-search\s*\{/,'messaging.css must not own the shared search control');
assert.match(forms,/(?:^|\n)\.settings-primary\s*\{/,'shared primary action belongs to components/forms.css');
assert.doesNotMatch(settings,/(?:^|\n)\.settings-primary\s*\{/,'settings.css must only contain Settings-specific primary-action modifiers');

assert.match(filters,/\.filter-card\s*\{/,'shared filter surface belongs to components/filters.css');
assert.match(filters,/\.location-fields\s*\{/,'shared location fields belong to components/filters.css');
assert.match(filters,/\.availability-note\s*\{/,'availability note styling must stay present');
assert.match(filters,/\.availability-quick button\s*\{[^}]*min-width:\s*76px;/s,'availability quick actions must keep their reviewed geometry');
assert.doesNotMatch(search,/(?:^|\n)\.filter-card\s*\{|(?:^|\n)\.location-fields\s*\{|(?:^|\n)\.settings-divider\s*\{/,'search.css must not redefine shared filter primitives');
assert.doesNotMatch(savedSearches,/(?:^|\n)\.filter-card\s*\{|(?:^|\n)\.location-fields\s*\{/,'saved-searches.css must compose shared filter primitives instead of redefining them');

assert.match(listingCard,/\.cards-grid\s*\{/,'shared listing grid belongs to components/listing-card.css');
assert.match(listingCard,/\.grid-card\s*\{/,'shared listing card belongs to components/listing-card.css');
assert.doesNotMatch(discover,/(?:^|\n)\.cards-grid\s*\{|(?:^|\n)\.grid-card\s*\{/,'discover.css must not own reusable listing-card primitives');

assert.match(priceList,/\.price-list-block\s*\{/,'shared price-list block belongs to components/price-list.css');
assert.match(priceList,/\.price-row\s*\{/,'shared price row belongs to components/price-list.css');
assert.doesNotMatch(pricing,/(?:^|\n)\.price-list-block\s*\{|(?:^|\n)\.price-row\s*\{/,'pricing.css must not own the shared profile/pricing list primitive');
assert.doesNotMatch(profile,/(?:^|\n)\.price-list-block\s*\{|(?:^|\n)\.price-row\s*\{/,'profile.css must compose the shared price-list primitive');

assert.match(history,/\.history-summary\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*repeat\(4,1fr\);/s,'History must own its summary geometry');
assert.match(history,/\.stat-card\s*\{/,'History must own its stat cards');
assert.match(history,/\.usage-grid\s*\{/,'History must own its usage summary');
assert.doesNotMatch(profile,/\.history-summary\b|\.stat-card\b|\.usage-grid\b/,'profile.css must not own History presentation');
assert.match(settings,/\.wa-status-dot\s*\{/,'Settings must own its WhatsApp status indicator');
assert.doesNotMatch(messaging,/\.wa-status-dot\b/,'messaging.css must not keep the retired WhatsApp status dependency');

assert.match(base,/:is\(a, button, input, textarea, select, summary, \[tabindex\]\):focus-visible\s*\{/,'foundation/base.css must provide a global keyboard focus treatment');
assert.match(base,/@media\s*\(prefers-reduced-motion:\s*reduce\)/,'foundation/base.css must respect the operating-system reduced-motion preference');

assert.match(base,/\.icon-button\s*\{[^}]*width:\s*42px;[^}]*height:\s*42px;/s,'icon button geometry must stay stable');
assert.match(discover,/\.swipe-media-panel\s*\{[^}]*min-height:\s*535px;/s,'Swipe media geometry must stay stable');
assert.match(listingCard,/\.grid-card\s*\{[^}]*aspect-ratio:\s*\.77;/s,'listing-card aspect ratio must stay stable');
assert.match(search,/\.search-screen \.screen-scroll\s*\{\s*padding:\s*12px var\(--screen-x\) 18px;\s*\}/,'Search viewport spacing must stay stable');
assert.match(profile,/\.profile-action-rail\s*\{[^}]*grid-template-columns:\s*repeat\(5,minmax\(0,1fr\)\);/s,'Profile action layout must stay five-column');
assert.match(messaging,/\.chat-screen\s*\{[^}]*grid-template-rows:\s*minmax\(0,1fr\) auto auto;/s,'Chat layout rows must stay stable');
assert.match(messaging,/\.chat-composer\s*\{[^}]*grid-template-columns:\s*36px minmax\(0,1fr\) 36px 46px;/s,'Chat composer geometry must stay stable');
assert.match(forms,/(?:^|\n)\.settings-primary\s*\{[^}]*min-height:\s*44px;/s,'shared primary actions must keep their touch target');

console.log('CSS architecture checks passed');
