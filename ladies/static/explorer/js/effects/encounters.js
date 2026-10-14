import { api } from '../api/client.js';
import { formEnumWireValue } from '../data/adapters.js';

export function createEncounterEffects({store,getEffects}) {
  const toast=message=>store.dispatch({type:'TOAST',value:message});

  function servicesFrom(form) {
    return [...form.querySelectorAll('[data-encounter-service-row]')].map(row=>{
      const name=String(row.querySelector('[name="service_name"]')?.value||'').trim();
      const amount=String(row.querySelector('[name="amount_eur"]')?.value||'').trim();
      const note=String(row.querySelector('[name="service_note"]')?.value||'').trim();
      const performed=Boolean(row.querySelector('[name="performed"]')?.checked);
      return name?{service_name:name,amount_eur:amount===''?null:Number(amount),performed,note:note||null}:null;
    }).filter(Boolean);
  }

  async function saveEncounter(form) {
    const personId=Number(form.dataset.personId||0);
    const encounterId=Number(form.dataset.encounterId||0);
    if(!personId)return toast('Person konnte nicht aufgelöst werden');
    const fd=new FormData(form);
    const selected=(kind,name)=>form.querySelector(`[data-${kind}-name="${name}"] .selected`)?.dataset.value || null;
    const enumChoice=name=>formEnumWireValue(name,selected('choice',name)||'UNKNOWN');
    const payload={
      listing_id:fd.get('listing_id')||null,
      occurred_at:fd.get('occurred_at')||null,
      duration_minutes:fd.get('duration_minutes')||null,
      base_price_eur:fd.get('base_price_eur')||null,
      total_price_eur:fd.get('total_price_eur')||null,
      overall_rating:selected('rating','overall_rating'),
      sympathy_rating:selected('rating','sympathy_rating'),
      photo_status:enumChoice('photo_status'),
      repeat_interest:enumChoice('repeat_interest'),
      service_accuracy:enumChoice('service_accuracy'),
      hygiene:enumChoice('hygiene'),
      kissing:enumChoice('kissing'),
      negotiable:enumChoice('negotiable'),
      private_note:String(fd.get('private_note')||'').trim()||null,
      note:String(fd.get('note')||'').trim()||null,
      services:servicesFrom(form)
    };
    try {
      if(encounterId)await api.updateEncounter(personId,encounterId,payload);
      else await api.addEncounter(personId,payload);
      await Promise.all([getEffects().loadPerson(personId,{force:true}),getEffects().loadDashboard()]);
      toast(encounterId?'Treffen aktualisiert':'Treffen gespeichert');
    } catch(error) { toast(error.message); }
  }

  async function saveEncounterServices(form) {
    if(form.dataset.fullEncounter==='true')return saveEncounter(form);
    const personId=Number(form.dataset.personId||0);
    const encounterId=Number(form.dataset.encounterId||0);
    if(!personId)return toast('Person konnte nicht aufgelöst werden');
    const services=servicesFrom(form);
    if(!encounterId&&!services.length)return toast('Mindestens einen Service eingeben');
    try {
      if(encounterId)await api.updateEncounter(personId,encounterId,{services});
      else await api.addEncounter(personId,{services});
      store.dispatch({type:'SHEET',value:null});
      await Promise.all([getEffects().loadPerson(personId,{force:true}),getEffects().loadDashboard()]);
      toast('Service-Details gespeichert');
    } catch(error) { toast(error.message); }
  }

  return Object.freeze({saveEncounterServices,saveEncounter});
}
