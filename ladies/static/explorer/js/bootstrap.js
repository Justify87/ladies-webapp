import { createStore } from './app/store.js';
import { createRouter } from './app/router.js';
import { createEffects } from './app/effects.js';
import { createCommands } from './app/commands.js';
import { createController } from './app/controller.js';
import { logClient } from './api/client.js';
import { installRegressionUi } from './regressions.js';
import { installUxPatch } from './ux_patch.js';
import { installSearchIntelligence } from './search_intelligence.js';
import { installRequestedFixes } from './requested_fixes.js';

const store=createStore();
const router=createRouter(store);
const effects=createEffects({store,router});
const commands=createCommands({store,router,effects});
installUxPatch({store});
createController({store,router,effects,commands});
installSearchIntelligence({store,effects,router});
installRegressionUi({store,effects});
installRequestedFixes({store,effects});
window.addEventListener('error',event=>logClient('error','window',event.message,{source:event.filename,line:event.lineno,column:event.colno}));
window.addEventListener('unhandledrejection',event=>logClient('error','promise',event.reason?.message||String(event.reason||'Unhandled rejection')));
const clock=document.querySelector('#statusClock');const updateClock=()=>{if(clock)clock.textContent=new Intl.DateTimeFormat('de-DE',{hour:'2-digit',minute:'2-digit'}).format(new Date());};updateClock();setInterval(updateClock,30000);
effects.boot();

window.__LADIES_EXPLORER_V13__=Object.freeze({store,router});
