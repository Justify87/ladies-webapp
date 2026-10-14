import assert from 'node:assert/strict';
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

console.log('search intelligence regression checks passed');
