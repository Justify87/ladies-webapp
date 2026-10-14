import { api } from '../api/client.js';
import * as S from '../app/selectors.js';

export function createPricingEffects({store,getEffects}) {
  const toast=message=>store.dispatch({type:'TOAST',value:message});
  const state=()=>store.getState();

  async function loadPricingSettings(){try{const [settings,runtime]=await Promise.all([api.pricingSettings(),api.pricingStatus().catch(()=>({}))]);store.dispatch({type:'PRICING_SETTINGS',value:settings?.settings||settings||{}});store.dispatch({type:'PRICING_RUNTIME',value:runtime||{});}catch{} }
  async function loadPricingForThread(threadId){try{const [signal,timeline]=await Promise.all([api.pricingSignal(threadId).catch(()=>({})),api.pricingTimeline(threadId,100).catch(()=>({items:[]}))]);store.dispatch({type:'PRICING_THREAD',threadId,value:{threadId,signal:signal?.signal||signal,timeline:timeline?.items||timeline}});}catch{} }

  async function openPricing(threadId,messageId=null){try{let proposal=await api.createPriceProposal(threadId,{trigger_type:messageId?'MESSAGE':'MANUAL',seed_message_id:messageId||null});if(proposal?.price_extraction_id)proposal=await api.priceProposal(proposal.price_extraction_id);store.dispatch({type:'PRICING_PROPOSAL',value:proposal});}catch(error){toast(error.message);} }
  async function openExistingPricing(extractionId){try{const proposal=await api.priceProposal(extractionId);store.dispatch({type:'PRICING_PROPOSAL',value:proposal});}catch(error){toast(error.message);} }
  async function openPersonPricing(personId){try{const [current,history]=await Promise.all([api.currentPricing(personId),api.pricingHistory(personId,30)]);store.dispatch({type:'SHEET',value:{kind:'person-pricing',payload:{personId,current,history:history?.items||[]}}});}catch(error){toast(error.message);} }

  async function acceptPricing(form){
    const id=Number(form.dataset.extractionId),extraction=state().pricing.proposal;
    if(!id||!extraction?.proposal)return toast('Preisvorschlag fehlt');
    const proposal=structuredClone(extraction.proposal);
    try{
      await api.acceptPriceProposal(id,{proposal});
      const t=S.currentThread(state());
      if(t)await loadPricingForThread(t.threadId);
      store.dispatch({type:'PRICING_PROPOSAL',value:null});
      toast('Preise gespeichert');
      const route=S.currentRoute(state());
      if(route.params?.personId)await getEffects().loadPerson(route.params.personId,{force:true});
    }catch(error){toast(error.message);}
  }

  async function revisePricing(id){
    try{
      const proposal=await api.revisePriceProposal(id);
      store.dispatch({type:'PRICING_PROPOSAL',value:proposal});
      const t=S.currentThread(state());if(t)await loadPricingForThread(t.threadId);
      toast('Preisvorschlag kann geändert werden');
    }catch(error){toast(error.message);}
  }

  async function rejectPricing(id){try{await api.rejectPriceProposal(id);store.dispatch({type:'PRICING_PROPOSAL',value:null});toast('Preisvorschlag gelöscht');const t=S.currentThread(state());if(t)await loadPricingForThread(t.threadId);}catch(error){toast(error.message);} }
  async function sendPresence(threadId,stateValue){const t=S.currentThread(state());const endpointId=Number(t?.selectedEndpoint?.endpoint_id||t?.selected_endpoint?.endpoint_id||t?.endpoint?.endpoint_id||t?.endpoints?.[0]?.endpoint_id||0);try{return await api.presence(threadId,stateValue,endpointId||null);}catch{return null;}}
  async function savePricingSettings(form){const payload={};for(const [key,value] of new FormData(form).entries())payload[key]=value;for(const input of form.querySelectorAll('input[type=checkbox]'))payload[input.name]=input.checked;try{const d=await api.setPricingSettings(payload);store.dispatch({type:'PRICING_SETTINGS',value:d?.settings||d||{}});store.dispatch({type:'SHEET',value:null});toast('Pricing-Einstellungen gespeichert');}catch(error){toast(error.message);} }

  return Object.freeze({loadPricingSettings,loadPricingForThread,openPricing,openExistingPricing,openPersonPricing,acceptPricing,revisePricing,rejectPricing,sendPresence,savePricingSettings});
}
