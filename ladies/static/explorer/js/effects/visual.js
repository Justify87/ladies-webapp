import { api } from '../api/client.js';
import * as S from '../app/selectors.js';
import { profileBundleVm } from '../data/adapters.js';

export function createVisualEffects({store,router,getEffects}) {
  let mediaPipe=null, mediaPipeLoading=null;
  const toast=message=>store.dispatch({type:'TOAST',value:message});
  const state=()=>store.getState();

  async function openArchive(kind) { try{const d=await api.archiveMedia(kind,200);router.push('archive',{kind,items:d?.items || []});}catch(error){toast(error.message);} }

  async function clearVisual(){try{const d=await api.clearVisual();toast(`${d.deleted||0} Analyse-Ergebnisse gelöscht`);await getEffects().loadSettings();}catch(error){toast(error.message);} }

  async function loadMediaPipe(){if(mediaPipe)return mediaPipe;if(mediaPipeLoading)return mediaPipeLoading;mediaPipeLoading=(async()=>{const mp=await import('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.1.0/vision_bundle.mjs');const vision=await mp.FilesetResolver.forVisionTasks('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.1.0/wasm');const face=await mp.FaceLandmarker.createFromOptions(vision,{baseOptions:{modelAssetPath:'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task'},runningMode:'IMAGE',numFaces:2});const pose=await mp.PoseLandmarker.createFromOptions(vision,{baseOptions:{modelAssetPath:'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task'},runningMode:'IMAGE',numPoses:2,outputSegmentationMasks:false});const hand=await mp.HandLandmarker.createFromOptions(vision,{baseOptions:{modelAssetPath:'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task'},runningMode:'IMAGE',numHands:4});mediaPipe={face,pose,hand};return mediaPipe;})().finally(()=>mediaPipeLoading=null);return mediaPipeLoading;}

  async function analyzeMedia(media,{notify=true}={}) { if(!media?.media_asset_id||String(media.kind).toUpperCase()!=='IMAGE')return;try{const models=await loadMediaPipe();const img=new Image();img.crossOrigin='anonymous';img.src=media.local_url||media.src||`/api/media-proxy?url=${encodeURIComponent(media.url||'')}`;await img.decode();const [f,p,h]=[models.face.detect(img),models.pose.detect(img),models.hand.detect(img)];const faces=f?.faceLandmarks?.length||0,poses=p?.landmarks?.length||0,hands=h?.landmarks?.length||0;let score=0,reasons=[];if(faces>1){score+=20;reasons.push('Mehr als ein Gesicht erkannt');}if(poses>1){score+=15;reasons.push('Mehr als eine Körperpose erkannt');}if(poses&&faces===0){score+=12;reasons.push('Körper erkannt, aber kein Gesicht');}if(faces&&poses===0){score+=8;reasons.push('Gesicht erkannt, aber keine klare Körperpose');}const label=score>=30?'prüfen':score>=12?'Hinweis':'unauffällig';await api.saveVisual(media.media_asset_id,{engine:'mediapipe-browser',engine_version:'1.1.0-v13-ui',score,label,reasons,diagnostics:{faces,poses,hands,width:img.naturalWidth,height:img.naturalHeight}});store.dispatch({type:'VISUAL_DONE',mediaId:media.media_asset_id,value:true});if(notify)toast(`Bildanalyse: ${label}`);await getEffects().loadSettings();const route=S.currentRoute(state());if(route.params?.personId)await getEffects().loadPerson(route.params.personId,{force:true});}catch(error){if(notify)toast(`Bildanalyse nicht verfügbar: ${error.message}`);} }

  async function analyzeCurrent(personId) { const detail=S.detailFor(state(),personId);if(!detail)return;const idx=S.profileMediaIndex(state(),personId);await analyzeMedia(detail.media[idx]);store.dispatch({type:'SHEET',value:null}); }

  async function autoAnalyzeCurrent() { const s=state();if(!s.settings.values.visual_check_enabled)return;const route=S.currentRoute(s);let detail=null;if(route.name==='profile')detail=S.detailFor(s,Number(route.params?.personId));else if(route.name==='swipe'){const row=S.currentSearchRow(state());const vm=row?profileBundleVm(s.profiles.bundles[row.advertId],row):null;detail=vm?.personId?S.detailFor(s,vm.personId):null;}if(!detail)return;const idx=S.profileMediaIndex(s,detail.personId);const media=detail.media[idx];if(media?.media_asset_id&&!media.visual_analysis&&!s.visual.done[String(media.media_asset_id)])analyzeMedia(media,{notify:false}); }

  async function toggleSetting(key,value) { try{if(key==='whatsapp_typing_enabled'){const d=await api.messagingSettings({typing_enabled:value});store.dispatch({type:'MESSAGE_STATUS',value:d});}else{const d=await api.setSetting(key,value);store.dispatch({type:'SETTINGS',values:d?.settings || state().settings.values});if(key==='reduced_motion')document.documentElement.classList.toggle('reduced-motion',!!value);}toast('Einstellung gespeichert');}catch(error){toast(error.message);} }

  async function saveDefaultFilters(){try{const d=await api.setSetting('default_filters',state().search.filters);store.dispatch({type:'SETTINGS',values:d?.settings||{}});toast('Standardfilter gespeichert');}catch(error){toast(error.message);} }

  async function clearDefaultFilters(){try{const d=await api.setSetting('default_filters',null);store.dispatch({type:'SETTINGS',values:d?.settings||{}});toast('Standardfilter gelöscht');}catch(error){toast(error.message);} }

  return Object.freeze({openArchive,clearVisual,loadMediaPipe,analyzeMedia,analyzeCurrent,autoAnalyzeCurrent,toggleSetting,saveDefaultFilters,clearDefaultFilters});
}
