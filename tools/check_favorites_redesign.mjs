import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window={__LADIES_CONFIG__:{}};
const { initialState }=await import('../ladies/static/explorer/js/app/state.js');
const { reducer }=await import('../ladies/static/explorer/js/app/reducer.js');
const { renderFavorites }=await import('../ladies/static/explorer/js/ui/screens/favorites.js');

let state=structuredClone(initialState);
state.lists.items=[{
  person_id:1,
  latest_advert_id:'100',
  name:'Anna',
  city:'Leipzig',
  favorite:true,
  preference:'LIKE',
  status:'ACTIVE',
  listing_count:1,
  last_seen_at:new Date(Date.now()-2*60*60*1000).toISOString(),
  recent_change:{event_type:'LISTING_FIRST_SNAPSHOT',created_at:new Date(Date.now()-60*60*1000).toISOString()},
}];

state=reducer(state,{type:'LIST_TAB',tab:'LIKE'});
assert.equal(state.lists.tab,'LIKE');
assert.deepEqual(state.lists.items,[],'tab switch must invalidate rows from the previous tab immediately');
assert.equal(state.lists.loading,true,'tab switch keeps the replacement list in an explicit loading state');
state=reducer(state,{type:'LIST_LOADING',value:false});
assert.deepEqual(state.lists.items,[],'a failed list request must not restore rows from the previous tab');

state.lists={
  ...state.lists,
  tab:'FAVORITE',
  sort:'DATE',
  loading:false,
  items:[{
    person_id:1,
    latest_advert_id:'100',
    name:'Anna',
    city:'Leipzig',
    favorite:true,
    preference:'LIKE',
    status:'ACTIVE',
    listing_count:1,
    last_seen_at:new Date(Date.now()-2*60*60*1000).toISOString(),
    recent_change:{event_type:'LISTING_FIRST_SNAPSHOT',created_at:new Date(Date.now()-60*60*1000).toISOString()},
  }],
  statusCheck:{running:true,done:3,total:5,succeeded:2,failed:1,checkedAt:null},
};
const html=renderFavorites(state);
assert.match(html,/saved-status-progress/,'the current status-check progress must use the redesigned component');
assert.match(html,/3 von 5/);
assert.match(html,/1 Fehler/);
assert.match(html,/60 %/);
assert.match(html,/saved-person-menu/,'the ellipsis must open a real actions menu');
assert.match(html,/Status prüfen/);
assert.match(html,/saved-person-open/,'profile navigation must be a native button for keyboard access');
assert.doesNotMatch(html,/value="DISTANCE"/,'unsupported distance sorting must not be exposed');
assert.match(html,/vor 1 Std\.|vor 2 Std\./,'rows must keep a compact relative activity/change time');

const selectors=readFileSync(new URL('../ladies/static/explorer/js/app/selectors.js',import.meta.url),'utf8');
assert.doesNotMatch(selectors,/sort==='DISTANCE'/,'dead distance sorting code must be removed with the unsupported control');

const core=readFileSync(new URL('../ladies/static/explorer/js/effects/core.js',import.meta.url),'utf8');
assert.match(core,/listSeq=0/,'list requests need an explicit sequence guard');
assert.match(core,/sequence!==listSeq\|\|target!==state\(\)\.lists\.tab/,'late responses from an older tab must be ignored');

for(const path of [
  '../ladies/static/explorer/css/foundation/base.css',
  '../ladies/static/explorer/css/layout/navigation.css',
  '../ladies/static/explorer/css/components/forms.css',
  '../ladies/static/explorer/css/components/overlays.css',
  '../ladies/static/explorer/css/features/discover.css',
  '../ladies/static/explorer/css/features/search.css',
  '../ladies/static/explorer/css/features/saved-searches.css',
  '../ladies/static/explorer/css/features/profile.css',
  '../ladies/static/explorer/css/features/pricing.css',
  '../ladies/static/explorer/css/features/messaging.css',
  '../ladies/static/explorer/css/features/settings.css',
  '../ladies/static/explorer/css/features/history.css',
  '../ladies/static/explorer/css/discover-map.css',
]){
  const css=readFileSync(new URL(path,import.meta.url),'utf8');
  assert.doesNotMatch(css,/\.favorite-card|\.favorites-tabs|\.favorites-toolbar|\.status-check-card/,'legacy favorites CSS must not survive outside the canonical redesign stylesheet');
}
const redesign=readFileSync(new URL('../ladies/static/explorer/css/favorites-redesign.css',import.meta.url),'utf8');
assert.match(redesign,/\.saved-status-progress/);
assert.match(redesign,/width:44px;height:44px/,'primary compact actions retain a 44px touch target');

console.log('favorites redesign regression checks passed');
