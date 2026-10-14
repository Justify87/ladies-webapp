import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  availabilityMode,
  filtersWithAvailabilityMode,
  normalizeAvailabilityFilters,
  baselineFromResults,
  computeSavedSearchDelta
} from '../ladies/static/explorer/js/search_intelligence.js';

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

// Focused guards for the Explorer follow-up fixes.
const discoverSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/discover.js',import.meta.url),'utf8');
const chromeSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/chrome.js',import.meta.url),'utf8');
const encounterSource=readFileSync(new URL('../ladies/static/explorer/js/effects/encounters.js',import.meta.url),'utf8');
const requestedSource=readFileSync(new URL('../ladies/static/explorer/js/requested_fixes.js',import.meta.url),'utf8');
const canonicalPricingSource=readFileSync(new URL('../ladies/pricing/canonical.py',import.meta.url),'utf8');
const pricingWebSource=readFileSync(new URL('../ladies/pricing/web.py',import.meta.url),'utf8');
assert.match(discoverSource,/grid-card-heart[^>]+data-action="grid-like"/s,'visible Grid heart must trigger Like');
assert.doesNotMatch(discoverSource,/discover-mode-pills/,'availability controls must not be duplicated inside Grid or Swipe content');
assert.match(chromeSource,/header-discovery-modes/,'Swipe and Grid availability controls must live in the shared header');
assert.match(chromeSource,/route\.name==='swipe'.*data-route="grid".*gridGlyph\(\).*route\.name==='grid'.*data-route="swipe"/s,'shared header must expose both view switches with a real Grid glyph');
assert.match(encounterSource,/querySelector\('\[name="base_price_eur"\]'\)\)payload\.base_price_eur=/,'hidden base price must only be sent when present');
assert.match(encounterSource,/querySelector\('\[name="note"\]'\)\)payload\.note=/,'hidden note must only be sent when present');
assert.match(encounterSource,/querySelector\('\[data-encounter-service-row\]'\)\)payload\.services=/,'hidden services must only be sent when present');
assert.match(requestedSource,/mode==='NOW'\?'Jetzt laut Anzeige':mode==='TODAY'\?'Heute laut Anzeige':'Heute verfügbar'/,'availability label must respect the quick filter mode');
assert.match(requestedSource,/panel\.appendChild\(identity\)/,'Swipe identity must be anchored inside the image panel');
assert.match(requestedSource,/panel\.appendChild\(pill\)/,'availability badge must be anchored in the opposite image corner');
assert.match(requestedSource,/requested-new-encounter"><summary/,'new encounter capture must start collapsed');
assert.doesNotMatch(requestedSource,/requested-new-encounter"\s+open/,'new encounter capture must never render open by default');
assert.match(requestedSource,/data-requested-price-history/,'profile must provide a dedicated price history tab');
assert.match(requestedSource,/api\.currentPricing\(personId\).*api\.pricingHistory\(personId,100\)/s,'profile pricing must read exclusively through canonical pricing APIs');
assert.match(requestedSource,/removeCardsByHeading\(panel,\['Merkmale','Öffnungszeiten','Preislisten'\]\)/,'service tab must no longer own traits, opening hours or price lists');
assert.match(requestedSource,/removeCardsByHeading\(panel,\['Aktuelle Filter'\]\)/,'analysis must not duplicate current filters');
assert.match(canonicalPricingSource,/PriceList\.source == CANONICAL_PRICE_SOURCE/,'canonical pricing must filter legacy/manual price rows');
assert.match(canonicalPricingSource,/CANONICAL_PRICE_SOURCE = "WHATSAPP_EXTRACTED"/,'chat-extracted prices are the source of truth');
assert.match(pricingWebSource,/CanonicalPriceIntelligenceService\(database, data_dir\)/,'pricing routes must use the canonical chat pricing service');

console.log('search intelligence regression checks passed');
