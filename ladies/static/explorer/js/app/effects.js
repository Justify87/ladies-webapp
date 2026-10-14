import { createCoreEffects } from '../effects/core.js';
import { createVisualEffects } from '../effects/visual.js';
import { createMessagingEffects } from '../effects/messaging.js';
import { createPricingEffects } from '../effects/pricing.js';
import { createEncounterEffects } from '../effects/encounters.js';

export function createEffects({store,router}) {
  let effects=null;
  const getEffects=()=>effects;
  const core=createCoreEffects({store,router});
  const visual=createVisualEffects({store,router,getEffects});
  const pricing=createPricingEffects({store,getEffects});
  const messaging=createMessagingEffects({store,router,getEffects});
  const encounters=createEncounterEffects({store,getEffects});
  const boot=async()=>{
    await Promise.allSettled([
      core.loadSettings(), core.loadDashboard(), core.loadRecentSearches(),
      messaging.loadMessagingStatus(), messaging.loadThreads(), pricing.loadPricingSettings()
    ]);
    messaging.connectEvents();
    core.schedulePreview({immediate:true});
  };
  effects=Object.freeze({...core,...visual,...messaging,...pricing,...encounters,boot});
  return effects;
}
