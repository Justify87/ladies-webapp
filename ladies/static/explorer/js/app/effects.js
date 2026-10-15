import { createCoreEffects } from '../effects/core.js';
import { createMapEffects } from '../effects/map.js';
import { createVisualEffects } from '../effects/visual.js';
import { createMessagingEffects } from '../effects/messaging.js';
import { createPricingEffects } from '../effects/pricing.js';
import { createEncounterEffects } from '../effects/encounters.js';
import { createProfileRequestEffects } from '../effects/profile_request.js';
import { createRegressionEffects } from '../regressions.js';
import { createRequestedEffects } from '../requested_fixes.js';

export function createEffects({store,router}) {
  let effects=null;
  const getEffects=()=>effects;
  const core=createCoreEffects({store,router});
  const map=createMapEffects({store});
  const regressions=createRegressionEffects({store,router,core});
  const requested=createRequestedEffects({store,core});
  const visual=createVisualEffects({store,router,getEffects});
  const pricing=createPricingEffects({store,getEffects});
  const messaging=createMessagingEffects({store,router,getEffects});
  const encounters=createEncounterEffects({store,getEffects});
  const profileRequests=createProfileRequestEffects({store});
  const openProfile=async args=>{
    await core.openProfile(args);
    const route=store.getState().navigation.stack.at(-1);
    const personId=Number(route?.name==='profile'?route.params?.personId:0);
    if(personId)await profileRequests.loadProfileRequest(personId);
  };
  const boot=async()=>{
    await Promise.allSettled([
      core.loadSettings(), core.loadDashboard(), core.loadRecentSearches(),
      messaging.loadMessagingStatus(), messaging.loadThreads(), pricing.loadPricingSettings()
    ]);
    messaging.connectEvents();
    core.schedulePreview({immediate:true});
  };
  effects=Object.freeze({...core,...map,...regressions,...requested,...visual,...messaging,...pricing,...encounters,...profileRequests,openProfile,schedulePreview:core.schedulePreview,boot});
  return effects;
}
