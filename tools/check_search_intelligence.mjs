import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  availabilityMode,
  filtersWithAvailabilityMode,
  normalizeAvailabilityFilters,
  baselineFromResults,
  computeSavedSearchDelta
} from '../ladies/static/explorer/js/search_intelligence.js';
import { threadRows, unreadTotal } from '../ladies/static/explorer/js/app/selectors.js';
import { canonicalPriceRows, invalidatesProfilePricing } from '../ladies/static/explorer/js/requested_fixes.js';

// Canonical availability slugs must be the only values written back to state.
assert.equal(availabilityMode({selected:{zeiten:['jetzt-offen']}}),'NOW');
assert.equal(availabilityMode({selected:{zeiten:['heute-offen']}}),'TODAY');
assert.equal(availabilityMode({selected:{zeiten:['jetzt geöffnet']}}),'NOW');
assert.deepEqual(
  normalizeAvailabilityFilters({selected:{zeiten:['heute geöffnet','nach-22']}}).selected.zeiten,
  ['nach-22','heute-offen']
);
assert.deepEqual(
  filtersWithAvailabilityMode({selected:{zeiten:['jetzt-offen','nach-22']}},'TODAY').selected.zeiten,
  ['nach-22','heute-offen']
);
assert.deepEqual(
  filtersWithAvailabilityMode({selected:{zeiten:['heute-offen','nach-22']}},'ALL').selected.zeiten,
  ['nach-22']
);

const previousResults=[
  {listing_id:1,person_id:101,advert_id:'1001',name:'A'},
  {listing_id:2,person_id:202,advert_id:'1002',name:'B'}
];
const baseline=baselineFromResults(previousResults,'2026-10-10T10:00:00+00:00');
const saved={
  baseline,
  // Listing 3 was part of this saved search in an older run, but not the immediately previous run.
  seen_listing_ids:[1,2,3]
};
const currentResults=[
  // Deliberately stale/provisional person IDs; context must win.
  {listing_id:2,person_id:999,advert_id:'1002',name:'B'},
  {listing_id:3,person_id:998,advert_id:'1003',name:'C'},
  // New advert, but identity resolves to already-known person 202.
  {listing_id:4,person_id:997,advert_id:'1004',name:'B neu'},
  // Truly new person.
  {listing_id:5,person_id:996,advert_id:'1005',name:'D'}
];
const context={
  canonical_person_by_listing:{'1':101,'2':202,'3':303,'4':202,'5':404},
  observed_changed_person_ids:[202,404]
};
const delta=computeSavedSearchDelta(saved,currentResults,context,'2026-10-11T10:00:00+00:00',0);
assert.equal(delta.initial,false);
assert.equal(delta.new_people_count,1,'only canonical person 404 is genuinely new');
assert.equal(delta.returned_people_count,1,'canonical person 303 returned after an older saved-search appearance');
assert.equal(delta.known_people_new_listing_count,1,'listing 4 belongs to already-known canonical person 202');
assert.equal(delta.disappeared_people_count,1,'canonical person 101 disappeared from the source result');
assert.equal(delta.observed_changed_people_count,2,'only scoped observed profile changes are counted');
assert.equal(delta.new_listings_count,2,'listings 4 and 5 were never seen by this saved search');

// Local UI filtering must not affect baseline construction: the caller passes raw search results.
const rawBaseline=baselineFromResults([
  {listing_id:7,person_id:707,advert_id:'1007',preference:'DISLIKE'},
  {listing_id:8,person_id:808,advert_id:'1008',has_video:false}
]);
assert.deepEqual(rawBaseline.listings.map(item=>item.listing_id),[7,8]);

// Message filters are local views over one canonical all-thread collection.
const messageState={messaging:{query:'',viewFilter:'UNASSIGNED',threads:[
  {thread_id:1,person_id:11,display_name:'A',unread_count:2},
  {thread_id:2,person_id:22,display_name:'B',unread_count:3},
  {thread_id:3,person_id:null,display_name:'C',unread_count:1}
]}};
assert.deepEqual(threadRows(messageState).map(row=>row.threadId),[3],'UNASSIGNED must only filter the visible list locally');
assert.equal(unreadTotal(messageState),6,'global unread count must still include threads hidden by the active view filter');

// Profile pricing must render every monetary component from the canonical proposal.
const canonicalRows=canonicalPriceRows({
  offers:[{label:'30 Minuten',amount_cents:10000}],
  service_rules:[{raw_label:'Massage',status:'SURCHARGE',amount_cents:2000},{raw_label:'Küssen',status:'INCLUDED',amount_cents:null}],
  conditions:[{label:'Hausbesuch',amount_cents:5000},{label:'Nur nach Absprache',amount_cents:null}]
});
assert.deepEqual(canonicalRows.map(row=>row.amount_cents),[10000,2000,5000],'offers, service surcharges and monetary conditions must all be visible');
assert.match(canonicalRows[1].label,/Massage/,'service price rows keep their service label');
assert.match(canonicalRows[2].label,/Hausbesuch/,'condition price rows keep their condition label');
assert.equal(invalidatesProfilePricing('PRICING_THREAD'),true);
assert.equal(invalidatesProfilePricing('PRICING_PROPOSAL'),true);
assert.equal(invalidatesProfilePricing('PROFILE_TAB'),false);

// Focused guards for the Explorer follow-up fixes.
const discoverSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/discover.js',import.meta.url),'utf8');
const profileSource=readFileSync(new URL('../ladies/static/explorer/js/ui/profile.js',import.meta.url),'utf8');
const prototypeSource=readFileSync(new URL('../ladies/static/explorer/css/prototype.css',import.meta.url),'utf8');
const chromeSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/chrome.js',import.meta.url),'utf8');
const clientSource=readFileSync(new URL('../ladies/static/explorer/js/api/client.js',import.meta.url),'utf8');
const encounterSource=readFileSync(new URL('../ladies/static/explorer/js/effects/encounters.js',import.meta.url),'utf8');
const requestedSource=readFileSync(new URL('../ladies/static/explorer/js/requested_fixes.js',import.meta.url),'utf8');
const canonicalPricingSource=readFileSync(new URL('../ladies/pricing/canonical.py',import.meta.url),'utf8');
const pricingWebSource=readFileSync(new URL('../ladies/pricing/web.py',import.meta.url),'utf8');
assert.match(discoverSource,/grid-card-heart[^>]+data-action="grid-like"/s,'visible Grid heart must trigger Like');
assert.doesNotMatch(discoverSource,/discover-mode-pills/,'availability controls must not be duplicated inside Grid or Swipe content');
assert.match(chromeSource,/header-discovery-modes/,'Swipe and Grid availability controls must live in the shared header');
assert.match(chromeSource,/route\.name==='swipe'.*data-route="grid".*gridGlyph\(\).*route\.name==='grid'.*data-route="swipe"/s,'shared header must expose both view switches with a real Grid glyph');
assert.match(clientSource,/threads:\s*\(\)\s*=>\s*request\('\/api\/messaging\/threads\?filter=ALL'\)/,'thread API must always load the canonical unfiltered collection');
assert.match(encounterSource,/querySelector\('\[name="base_price_eur"\]'\)\)payload\.base_price_eur=/,'hidden base price must only be sent when present');
assert.match(encounterSource,/querySelector\('\[name="note"\]'\)\)payload\.note=/,'hidden note must only be sent when present');
assert.match(encounterSource,/querySelector\('\[data-encounter-service-row\]'\)\)payload\.services=/,'hidden services must only be sent when present');
assert.match(profileSource,/function renderProfileBody\(/,'standalone and swipe profiles must share one profile body renderer');
assert.match(profileSource,/screen-view standalone-profile swipe-profile/,'Swipe must use the canonical standalone profile surface');
assert.doesNotMatch(profileSource,/swipe-details/,'retired Swipe-only detail markup must not return');
assert.doesNotMatch(requestedSource,/cleanSwipe|requested-swipe-identity|requested-profile-availability|availabilityMode/,'legacy Swipe DOM rewriting must stay removed');
assert.match(prototypeSource,/\.swipe-profile \.screen-scroll/,'Swipe keeps its media-sized scroll surface through the canonical Swipe profile class');
assert.doesNotMatch(prototypeSource,/swipe-details|profile-media::after/,'legacy Swipe detail styling and dark media overlay must stay removed');
assert.match(requestedSource,/requested-new-encounter"><summary/,'new encounter capture must start collapsed');
assert.doesNotMatch(requestedSource,/requested-new-encounter"\s+open/,'new encounter capture must never render open by default');
assert.match(requestedSource,/dataset\.requestedPriceHistory='1'/,'profile must provide a dedicated price history tab');
assert.match(requestedSource,/api\.currentPricing\(personId\).*api\.pricingHistory\(personId,100\)/s,'profile pricing must read exclusively through canonical pricing APIs');
assert.doesNotMatch(requestedSource,/15000/,'profile pricing must not use a time-based stale window');
assert.match(requestedSource,/if\(invalidatesProfilePricing\(action\?\.type\)\)pricingCache\.clear\(\)/,'pricing mutations must invalidate profile pricing immediately');
assert.match(requestedSource,/removeCardsByHeading\(panel,\['Merkmale','Öffnungszeiten','Preislisten'\]\)/,'service tab must no longer own traits, opening hours or price lists');
assert.match(requestedSource,/removeCardsByHeading\(panel,\['Aktuelle Filter'\]\)/,'analysis must not duplicate current filters');
assert.match(canonicalPricingSource,/PriceList\.source == CANONICAL_PRICE_SOURCE/,'canonical pricing must filter legacy/manual price rows');
assert.match(canonicalPricingSource,/CANONICAL_PRICE_SOURCE = "WHATSAPP_EXTRACTED"/,'chat-extracted prices are the source of truth');
assert.match(pricingWebSource,/CanonicalPriceIntelligenceService\(database, data_dir\)/,'pricing routes must use the canonical chat pricing service');

console.log('search intelligence regression checks passed');
