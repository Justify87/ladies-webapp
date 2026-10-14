import { createCoreEffects } from '../effects/core.js';
import { createVisualEffects } from '../effects/visual.js';
import { createMessagingEffects } from '../effects/messaging.js';
import { createPricingEffects } from '../effects/pricing.js';
import { createEncounterEffects } from '../effects/encounters.js';
import { createRegressionEffects } from '../regressions.js';

export function createEffects({store,router}) {
  let effects=null;
  let discoverBusy=false;
  const getEffects=()=>effects;
  const core=createCoreEffects({store,router});
  const regressions=createRegressionEffects({store,router,core});
  const visual=createVisualEffects({store,router,getEffects});
  const pricing=createPricingEffects({store,getEffects});
  const messaging=createMessagingEffects({store,router,getEffects});
  const encounters=createEncounterEffects({store,getEffects});

  const discoverDecision=async decision=>{
    if(discoverBusy)return;
    const row=core.rawSearchRowAt();
    if(!row)return;
    discoverBusy=true;
    try {
      const bundleVm=await core.loadBundle(row,{detail:false,quiet:false});
      const personId=Number(bundleVm?.personId||row.personId||0)||null;
      if(!personId)return core.toast('Person konnte nicht aufgelöst werden');
      const state=store.getState();
      const before={
        preference:bundleVm?.preference||row.preference||'NONE',
        favorite:bundleVm?.favorite??row.favorite??false,
        index:state.discover.index,
        personId
      };
      const replayLikes=state.settings.values.replay_likes!==false;
      const hideLikes=Boolean(state.search.filters.local?.hideLikes);
      const hideDislikes=state.search.filters.local?.hideDislikes!==false;
      if(decision==='LIKE')await core.mutatePerson(personId,{preference:'LIKE'});
      else if(decision==='DISLIKE')await core.mutatePerson(personId,{preference:'DISLIKE'});
      else if(decision==='FAVORITE')await core.mutatePerson(personId,{favorite:!before.favorite});
      store.dispatch({type:'DISCOVER_HISTORY_PUSH',entry:before});
      if(decision!=='FAVORITE'){
        const remains=(decision==='LIKE'&&!hideLikes&&replayLikes)||(decision==='DISLIKE'&&!hideDislikes);
        store.dispatch({type:'DISCOVER_INDEX',index:remains?before.index+1:before.index});
      }
      core.loadDashboard();
      void core.prefetchCurrent();
    } catch(error) {
      core.toast(error.message||String(error));
    } finally {
      discoverBusy=false;
    }
  };

  const boot=async()=>{
    await Promise.allSettled([
      core.loadSettings(), core.loadDashboard(), core.loadRecentSearches(),
      messaging.loadMessagingStatus(), messaging.loadThreads(), pricing.loadPricingSettings()
    ]);
    messaging.connectEvents();
    core.schedulePreview({immediate:true});
  };
  effects=Object.freeze({...core,...regressions,...visual,...messaging,...pricing,...encounters,discoverDecision,schedulePreview:core.schedulePreview,boot});
  return effects;
}
