import { api } from '../api/client.js';

export function createEncounterEffects({store,getEffects}) {
  const toast=message=>store.dispatch({type:'TOAST',value:message});

  async function saveEncounterServices(form) {
    const personId=Number(form.dataset.personId||0);
    const encounterId=Number(form.dataset.encounterId||0);
    if(!personId)return toast('Person konnte nicht aufgelöst werden');
    const services=[...form.querySelectorAll('[data-encounter-service-row]')].map(row=>{
      const name=String(row.querySelector('[name="service_name"]')?.value||'').trim();
      const amount=String(row.querySelector('[name="amount_eur"]')?.value||'').trim();
      const note=String(row.querySelector('[name="service_note"]')?.value||'').trim();
      const performed=Boolean(row.querySelector('[name="performed"]')?.checked);
      return name?{service_name:name,amount_eur:amount===''?null:Number(amount),performed,note:note||null}:null;
    }).filter(Boolean);
    try {
      if(encounterId)await api.updateEncounter(personId,encounterId,{services});
      else await api.addEncounter(personId,{services});
      store.dispatch({type:'SHEET',value:null});
      await getEffects().loadPerson(personId,{force:true});
      toast('Service-Details gespeichert');
    } catch(error) { toast(error.message); }
  }

  return Object.freeze({saveEncounterServices});
}
